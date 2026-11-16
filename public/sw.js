// Network-first for the app shell; YouTube and /api always go to the network.
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  e.respondWith(fetch(e.request).then(r => {
    if (r.ok) { const copy = r.clone(); caches.open('app').then(c => c.put(e.request, copy)); }
    return r;
  }).catch(() => caches.match(e.request)));
});
