"""Serves public/ and GET /api/subs?v=<id>[&lang=<key>] -> {title, channel, lang, langs, cues}.

Run: python server.py [port]   (needs yt-dlp: pip install yt-dlp)
"""
import json
import re
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from yt_dlp import YoutubeDL

PUBLIC = Path(__file__).parent / 'public'
VIDEO_ID = re.compile(r'^[\w-]{11}$')
YDL_OPTS = {'quiet': True, 'no_warnings': True, 'skip_download': True}
TTL = 3600  # caption URLs from YouTube expire after a few hours
_cache = {}  # video id -> (fetched_at, title, channel, language, tracks); ponytail: unbounded, add LRU if it ever runs for weeks


def tracks(vid):
    hit = _cache.get(vid)
    if hit and time.time() - hit[0] < TTL:
        return hit[1:]
    with YoutubeDL(YDL_OPTS) as ydl:
        info = ydl.extract_info(f'https://www.youtube.com/watch?v={vid}', download=False)
    out = {}  # key 'm:<code>' (manual) / 'a:<code>' (auto) -> {name, url}
    for kind, src in (('m', info.get('subtitles')), ('a', info.get('automatic_captions'))):
        for code, fmts in (src or {}).items():
            url = next((f['url'] for f in fmts if f.get('ext') == 'json3'), None)
            if url and code != 'live_chat':
                out[f'{kind}:{code}'] = {'name': fmts[0].get('name') or code, 'url': url}
    _cache[vid] = (time.time(), info.get('title') or vid, info.get('channel') or info.get('uploader') or '', info.get('language'), out)
    return _cache[vid][1:]


def pick(keys, orig):
    """Manual English > manual original language > any manual > auto original > auto English."""
    en = sorted(k for k in keys if k.startswith('m:en'))
    manual = [k for k in keys if k.startswith('m:')]
    for k in [*en, f'm:{orig}', *manual, f'a:{orig}-orig', f'a:{orig}', 'a:en-orig', 'a:en']:
        if k in keys:
            return k
    return next(iter(keys), None)


def parse_json3(data):
    cues = []
    for e in data.get('events', []):
        text = ' '.join(''.join(s.get('utf8', '') for s in e.get('segs') or []).split())
        if text:
            start = e['tStartMs'] / 1000
            cues.append({'start': start, 'end': start + e.get('dDurationMs', 0) / 1000, 'text': text})
    # auto captions roll: each line stays on screen while the next is spoken, so clip to the next start
    for a, b in zip(cues, cues[1:]):
        a['end'] = min(a['end'], b['start'])
    return cues


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=PUBLIC, **kw)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path != '/api/subs':
            return super().do_GET()
        q = parse_qs(url.query)
        vid, lang = q.get('v', [''])[0], q.get('lang', [''])[0]
        if not VIDEO_ID.match(vid):
            return self.send_json(400, {'error': 'Invalid video id'})
        try:
            title, channel, orig, subs = tracks(vid)
            if lang not in subs:
                lang = pick(subs, orig)
            if not lang:
                return self.send_json(404, {'error': 'This video has no subtitles'})
            with YoutubeDL(YDL_OPTS) as ydl:
                cues = parse_json3(json.loads(ydl.urlopen(subs[lang]['url']).read()))
        except Exception as e:
            return self.send_json(502, {'error': str(e).removeprefix('ERROR: ')})
        langs = [{'key': k, 'name': v['name']} for k, v in subs.items()]
        self.send_json(200, {'title': title, 'channel': channel, 'lang': lang, 'langs': langs, 'cues': cues})

    def send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    assert pick({'a:en': 1, 'm:fr': 1}, 'en') == 'm:fr'
    assert pick({'a:ja-orig': 1, 'a:en': 1}, 'ja') == 'a:ja-orig'
    assert parse_json3({'events': [{'tStartMs': 0, 'dDurationMs': 5000, 'segs': [{'utf8': 'a\nb'}]},
                                   {'tStartMs': 2000, 'segs': [{'utf8': '\n'}]},
                                   {'tStartMs': 3000, 'dDurationMs': 1000, 'segs': [{'utf8': 'c'}]}]}) == \
        [{'start': 0, 'end': 3, 'text': 'a b'}, {'start': 3, 'end': 4, 'text': 'c'}]
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f'http://0.0.0.0:{port}')
    ThreadingHTTPServer(('', port), Handler).serve_forever()
