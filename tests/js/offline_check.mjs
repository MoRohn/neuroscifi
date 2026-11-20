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
await lifecycle("install");await lifecycle("activate");
assert(stores.has("unrelated"));assert(!stores.has("neuroscifi-pocket-shell-old"));
async function read(path,method="GET"){let answer;callbacks.fetch({request:{url:"https://example.test"+path,method},respondWith:p=>answer=p});return answer;}
assert.match(await read("/"),/Field session/);assert.match(await read("/field-ui.mjs"),/restore|restored/i);
assert.equal(await read("/api/episodes","POST"),undefined);assert.equal(await read("/?code=example"),undefined);
console.log("Passed deterministic offline asset replay, cache upgrade, private API exclusion and URL query exclusion.");
