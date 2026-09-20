const CACHE_NAME='smart-azan-v2';const ASSETS=['/','/static/manifest.webmanifest','/static/css/mobile.css','/static/js/mobile.js'];
self.addEventListener('install',e=>{e.waitUntil(caches.open(CACHE_NAME).then(c=>c.addAll(ASSETS)));self.skipWaiting()});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(keys=>Promise.all(keys.map(k=>k===CACHE_NAME?null:caches.delete(k)))).then(()=>self.clients.claim()))});
self.addEventListener('fetch',e=>{const r=e.request;if(r.method!=='GET')return;const url=new URL(r.url);const isAPI=url.pathname.startsWith('/bt_')||url.pathname.startsWith('/wifi')||url.pathname.startsWith('/hotspot')||url.pathname.startsWith('/set_volume');if(isAPI)return;
// Network-first, not cache-first: this app changes often, and a stale cached
// page/stylesheet looking like a fix "reverted" is worse than an extra
// round-trip. Cache is only a fallback for when the server is briefly
// unreachable (e.g. mid Wi-Fi reconnect), never the default source of truth.
e.respondWith(fetch(r).then(resp=>{if(resp.ok&&r.url.startsWith(self.location.origin)){const cl=resp.clone();caches.open(CACHE_NAME).then(cache=>cache.put(r,cl))}return resp}).catch(()=>caches.match(r).then(c=>c||caches.match('/'))))});

// Web Push - this is what lets a reminder/azan notification reach the
// device even with the app's tab closed, unlike the old
// Notification.requestPermission()-only approach which only ever fired
// while a tab was open and active.
self.addEventListener('push', e => {
  let data = {title: 'Smart Azan', body: ''};
  try { data = e.data.json(); } catch (err) {}
  e.waitUntil(self.registration.showNotification(data.title, {
    body: data.body,
    tag: data.tag,
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
    // Carried through to notificationclick below - play_url (set only for
    // actual azan pushes, not reminders) is what lets tapping the
    // notification actually play the azan out loud on this phone.
    data: {play_url: data.play_url || null},
  }));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const target = e.notification.data && e.notification.data.play_url;
  e.waitUntil(self.clients.matchAll({type: 'window'}).then(list => {
    // A play_url always opens fresh (it needs its own autoplay attempt on
    // load) rather than reusing/focusing an already-open tab.
    if (target && self.clients.openWindow) return self.clients.openWindow(target);
    for (const c of list) { if ('focus' in c) return c.focus(); }
    if (self.clients.openWindow) return self.clients.openWindow('/');
  }));
});
