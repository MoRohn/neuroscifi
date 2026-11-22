// Exercise the shipped service worker's install, activate and offline request paths.
import assert from "node:assert/strict";
import vm from "node:vm";
import {readFileSync} from "node:fs";
const callbacks={},stores=new Map([["unrelated",new Map()],["neuroscifi-pocket-shell-old",new Map()]]);
const saved=async name=>{
 if(!stores.has(name))stores.set(name,new Map());
 const objects=stores.get(name);
 return {async addAll(paths){for(const path of paths){
   const file=path==="/"?"pocket/web/index.html":path.startsWith("/presence/")?"presence/web/"+path.slice(10):
     path.startsWith("/voice/")?"voice/web/"+path.slice(7):"pocket/web/"+path.slice(1);
   const text=readFileSync(new URL("../../src/neuroscifi/"+file,import.meta.url),"utf8");
   objects.set(path,text);
 }},async match(path){return objects.get(path);}};
};
const shown=[],told=[];
const ctx=vm.createContext({self:{addEventListener:(k,fn)=>callbacks[k]=fn,async skipWaiting(){},
  clients:{async claim(){},async matchAll(){return [{postMessage:m=>told.push(m)}];}},
  registration:{async showNotification(title,options){shown.push({title,...options});}}},
  caches:{open:saved,keys:async()=>[...stores.keys()],delete:async key=>stores.delete(key)},URL,location:{origin:"https://example.test"},
  fetch(){throw Error("Network offline");}});
vm.runInContext(readFileSync(new URL("../../src/neuroscifi/pocket/web/sw.js",import.meta.url),"utf8"),ctx);
async function lifecycle(name){let work;callbacks[name]({waitUntil:p=>work=p});await work;}
async function read(path,method="GET"){let answer;callbacks.fetch({request:{url:"https://example.test"+path,method},respondWith:p=>answer=p});return answer;}
await lifecycle("install");await lifecycle("activate");
const installed=[...stores.get("neuroscifi-pocket-shell-24").keys()];
// Plan p.20 names six shell assets; the manifest and its icon make eight (ADR-015) and the Research module nine
// (ADR-028: saved findings must reopen offline), its node-key module ten (ADR-042 draft: the research module imports
// it), plus the 16 Presence Cortex/theme files (docs/presence/) and the 7 Voice Cortex files (docs/voice/; vad.mjs and
// voice-check.mjs since the Voice check), plus the shared design system (/presence/ui.css, docs/ui/DESIGN_SYSTEM.md) and the
// maskable home-screen icon (Android adaptive icons crop to a circle or squircle). Deviations.
assert.equal(installed.length,35);assert(installed.includes("/presence/ui.css")&&installed.includes("/icon-maskable.svg"));assert(installed.includes("/node-key.mjs"));assert(installed.includes("/voice/pocket-voice.mjs"));assert(installed.includes("/voice/vad.mjs")&&installed.includes("/voice/voice-check.mjs"));assert(installed.includes("/presence/pocket-boot.mjs")&&installed.includes("/presence/theme-boot.js"));assert(installed.includes("/manifest.webmanifest")&&installed.includes("/icon.svg"));
assert(installed.includes("/research-ui.mjs"));assert.match(await read("/research-ui.mjs"),/keepFinding/);assert.match(await read("/research-ui.mjs"),/research_session/);
assert.equal(await read("/api/research_get","POST"),undefined);
const manifest=JSON.parse(await read("/manifest.webmanifest"));
assert.equal(manifest.shortcuts.length,3);assert.deepEqual(manifest.share_target,{action:"/",method:"GET",params:{title:"share_title",text:"share_text",url:"share_url"}});
assert.equal(await read("/?share_url=https%3A%2F%2Fexample.org%2Fa"),undefined);assert(callbacks.push,"push handler (ADR-040)");assert.match(await read("/icon.svg"),/<svg/);
assert(stores.has("unrelated"));assert(!stores.has("neuroscifi-pocket-shell-old"));
assert.match(await read("/"),/Field session/);assert.match(await read("/field-ui.mjs"),/restore|restored/i);
assert.equal(await read("/api/episodes","POST"),undefined);assert.equal(await read("/?code=example"),undefined);
// An empty Web Push (ADR-040) shows the same generic text whatever arrives, and tells open pages to refresh.
for(const data of [null,{text:()=>"What is the latest version of the Zephyr compiler?"}]){
  let work;callbacks.push({data,waitUntil:p=>work=p});await work;
}
assert.equal(shown.length,2);
for(const n of shown)assert.deepEqual(JSON.parse(JSON.stringify(n)),{title:"NeuroSciFi Pocket",body:"Your Spark finished a research task.",tag:"pocket-push",icon:"/icon.svg"});
assert.equal(JSON.stringify(told),JSON.stringify([{type:"pocket-push"},{type:"pocket-push"}]));  // objects from the vm realm
console.log("Passed generic empty-push notification and page refresh message.");
console.log("Passed deterministic offline asset replay, 35-asset shell (eleven app assets with manifest (share target), icon and maskable icon, research and node-key modules, plus 17 presence/theme/design-system and 7 voice files), cache upgrade, private API exclusion and URL query exclusion.");
