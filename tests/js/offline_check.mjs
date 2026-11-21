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
const ctx=vm.createContext({self:{addEventListener:(k,fn)=>callbacks[k]=fn,async skipWaiting(){},clients:{async claim(){}}},
  caches:{open:saved,keys:async()=>[...stores.keys()],delete:async key=>stores.delete(key)},URL,location:{origin:"https://example.test"},
  fetch(){throw Error("Network offline");}});
vm.runInContext(readFileSync(new URL("../../src/neuroscifi/pocket/web/sw.js",import.meta.url),"utf8"),ctx);
async function lifecycle(name){let work;callbacks[name]({waitUntil:p=>work=p});await work;}
async function read(path,method="GET"){let answer;callbacks.fetch({request:{url:"https://example.test"+path,method},respondWith:p=>answer=p});return answer;}
await lifecycle("install");await lifecycle("activate");
const installed=[...stores.get("neuroscifi-pocket-shell-13").keys()];
// Plan p.20 names six shell assets; the manifest and its icon make eight (ADR-015) and the Research module nine
// (ADR-028: saved findings must reopen offline), plus the 16 Presence Cortex/theme files (docs/presence/) and the 5
// Voice Cortex files (docs/voice/). Deviations.
assert.equal(installed.length,30);assert(installed.includes("/voice/pocket-voice.mjs"));assert(installed.includes("/presence/pocket-boot.mjs")&&installed.includes("/presence/theme-boot.js"));assert(installed.includes("/manifest.webmanifest")&&installed.includes("/icon.svg"));
assert(installed.includes("/research-ui.mjs"));assert.match(await read("/research-ui.mjs"),/keepFinding/);
assert.equal(await read("/api/research_get","POST"),undefined);
const manifest=JSON.parse(await read("/manifest.webmanifest"));
assert.equal(manifest.shortcuts.length,3);assert.deepEqual(manifest.share_target,{action:"/",method:"GET",params:{title:"share_title",text:"share_text",url:"share_url"}});
assert.equal(await read("/?share_url=https%3A%2F%2Fexample.org%2Fa"),undefined);assert(!callbacks.push,"no push handler");assert.match(await read("/icon.svg"),/<svg/);
assert(stores.has("unrelated"));assert(!stores.has("neuroscifi-pocket-shell-old"));
assert.match(await read("/"),/Field session/);assert.match(await read("/field-ui.mjs"),/restore|restored/i);
assert.equal(await read("/api/episodes","POST"),undefined);assert.equal(await read("/?code=example"),undefined);
console.log("Passed deterministic offline asset replay, 30-asset shell (nine app assets with manifest (share target), icon and research module, plus 16 presence and 5 voice files), cache upgrade, private API exclusion and URL query exclusion.");
