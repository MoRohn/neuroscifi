const CACHE="neuroscifi-pocket-shell-5";
const shell=["/","/style.css","/mobile.js","/field-ui.mjs","/field-model.mjs","/sw.js","/manifest.webmanifest","/icon.svg"];
self.addEventListener("install",e=>e.waitUntil(caches.open(CACHE).then(c=>c.addAll(shell)).then(()=>self.skipWaiting())));
self.addEventListener("activate",e=>e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith("neuroscifi-pocket-shell-")&&k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener("fetch",e=>{
 const u=new URL(e.request.url);
 if(e.request.method==="GET"&&u.origin===location.origin&&shell.includes(u.pathname)&&!u.search){
   e.respondWith(caches.open(CACHE).then(async c=>(await c.match(u.pathname))||fetch(e.request)));
 }
});
