const CACHE="neuroscifi-pocket-shell-27";
const shell=["/","/style.css","/mobile.js","/field-ui.mjs","/field-model.mjs","/research-ui.mjs","/node-key.mjs","/sw.js","/manifest.webmanifest","/icon.svg","/apple-touch-icon.png","/icon-192.png","/icon-512.png","/icon-maskable-512.png",
 "/presence/ui.css","/presence/theme-boot.js","/presence/theme.css","/presence/theme.mjs","/presence/contracts.mjs","/presence/geometry.mjs","/presence/canonical.mjs","/presence/composer.mjs","/presence/validator.mjs","/presence/interpreter.mjs","/presence/planner.mjs","/presence/motion.mjs","/presence/renderer.mjs","/presence/a11y.mjs","/presence/channel.mjs","/presence/presence.mjs","/presence/pocket-boot.mjs",
 "/voice/voice.css","/voice/voice-ui.mjs","/voice/voice-client.mjs","/voice/capture-worklet.mjs","/voice/pocket-voice.mjs",
 "/voice/vad.mjs","/voice/voice-check.mjs"];
self.addEventListener("install",e=>e.waitUntil(caches.open(CACHE).then(c=>c.addAll(shell)).then(()=>self.skipWaiting())));
self.addEventListener("activate",e=>e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith("neuroscifi-pocket-shell-")&&k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener("fetch",e=>{
 const u=new URL(e.request.url);
 if(e.request.method==="GET"&&u.origin===location.origin&&shell.includes(u.pathname)&&!u.search){
   e.respondWith(caches.open(CACHE).then(async c=>(await c.match(u.pathname))||fetch(e.request)));
 }
});
// Research notifications come from the open page (research-ui.mjs) or, for a device that opted in, from an empty Web
// Push (ADR-040). The push carries no data, and this worker holds no pairing credential (POC-07: tab memory only), so
// it cannot ask the Spark what finished: the text is always the same and Pocket loads the details when opened. Open
// pages are told so they refresh. A tap brings Pocket forward.
const PUSHED="Your Spark finished a research task";
self.addEventListener("push",e=>e.waitUntil(self.clients.matchAll({type:"window",includeUncontrolled:true}).then(list=>{
 for(const c of list)c.postMessage({type:"pocket-push"});
 return self.registration.showNotification("NeuroSciFi Pocket",{body:PUSHED+".",tag:"pocket-push",icon:"/icon-192.png"});
})));
self.addEventListener("notificationclick",e=>{e.notification.close();
 e.waitUntil(self.clients.matchAll({type:"window"}).then(list=>list.length?list[0].focus():self.clients.openWindow("/#research")));});
