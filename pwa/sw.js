// App shell cache so the recorder opens without a network. Uploads are not
// handled here: the page keeps unsent chunks in IndexedDB and retries.
const CACHE = 'minutes-pro-v6';
// The app lives at /app/ on the box or /indie-recorder/app/ behind brain-tls: paths from its scope.
const SCOPE = new URL(self.registration.scope).pathname;
const SHARED = new Set(['static/swiss.css', 'static/Inter.woff2', 'static/meetings.css', 'static/meetings.js'].map(p => SCOPE.replace(/app\/$/, '') + p));
const SHELL = ['./', 'index.html', 'app.js', 'app.css', 'manifest.webmanifest', 'icon-192.png', 'icon-512.png', ...SHARED];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || !(url.pathname.startsWith(SCOPE) || SHARED.has(url.pathname))) return;
  // Network first, so a new version shows up at once; the cache covers offline starts.
  e.respondWith(fetch(e.request)
    .then(r => { const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); return r; })
    .catch(() => caches.match(e.request).then(r => r || caches.match('index.html'))));
});
