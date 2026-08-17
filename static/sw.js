const CACHE_NAME='smart-azan-v2';const ASSETS=['/','/static/manifest.webmanifest','/static/css/mobile.css','/static/js/mobile.js'];
self.addEventListener('install',e=>{e.waitUntil(caches.open(CACHE_NAME).then(c=>c.addAll(ASSETS)));self.skipWaiting()});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(keys=>Promise.all(keys.map(k=>k===CACHE_NAME?null:caches.delete(k)))).then(()=>self.clients.claim()))});
self.addEventListener('fetch',e=>{const r=e.request;if(r.method!=='GET')return;const url=new URL(r.url);const isAPI=url.pathname.startsWith('/bt_')||url.pathname.startsWith('/wifi')||url.pathname.startsWith('/hotspot')||url.pathname.startsWith('/set_volume');if(isAPI)return;
// Network-first, not cache-first: this app changes often, and a stale cached
// page/stylesheet looking like a fix "reverted" is worse than an extra
// round-trip. Cache is only a fallback for when the server is briefly
// unreachable (e.g. mid Wi-Fi reconnect), never the default source of truth.
e.respondWith(fetch(r).then(resp=>{if(resp.ok&&r.url.startsWith(self.location.origin)){const cl=resp.clone();caches.open(CACHE_NAME).then(cache=>cache.put(r,cl))}return resp}).catch(()=>caches.match(r).then(c=>c||caches.match('/'))))});
