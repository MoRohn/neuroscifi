// Exercise the shipped service worker's install, activate and offline request paths.
import assert from "node:assert/strict";
import vm from "node:vm";
import {readFileSync} from "node:fs";
const callbacks={},stores=new Map([["unrelated",new Map()],["neuroscifi-pocket-shell-old",new Map()]]);
const saved=async name=>{
 if(!stores.has(name))stores.set(name,new Map());
 const objects=stores.get(name);
 return {async addAll(paths){for(const path of paths){
   const text=readFileSync(new URL("../../src/neuroscifi/pocket/web/"+(path==="/"?"index.html":path.slice(1)),import.meta.url),"utf8");
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
const installed=[...stores.get("neuroscifi-pocket-shell-6").keys()];
// Plan p.20 names six shell assets; the manifest and its icon make eight (ADR-015) and the Research module nine
// (ADR-028: saved findings must reopen offline). Documented deviations.
assert.equal(installed.length,9);assert(installed.includes("/manifest.webmanifest")&&installed.includes("/icon.svg"));
assert(installed.includes("/research-ui.mjs"));assert.match(await read("/research-ui.mjs"),/keepFinding/);
assert.equal(await read("/api/research_get","POST"),undefined);
assert.equal(JSON.parse(await read("/manifest.webmanifest")).shortcuts.length,3);assert.match(await read("/icon.svg"),/<svg/);
assert(stores.has("unrelated"));assert(!stores.has("neuroscifi-pocket-shell-old"));
assert.match(await read("/"),/Field session/);assert.match(await read("/field-ui.mjs"),/restore|restored/i);
assert.equal(await read("/api/episodes","POST"),undefined);assert.equal(await read("/?code=example"),undefined);
console.log("Passed deterministic offline asset replay, nine-asset shell with manifest, icon and research module, cache upgrade, private API exclusion and URL query exclusion.");
