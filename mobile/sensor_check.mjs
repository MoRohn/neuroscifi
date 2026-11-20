// Run the shipped UI code with synthetic media, DOM events, a virtual clock and an inspected transport.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {randomUUID} from "node:crypto";
import vm from "node:vm";
const ui=readFileSync(new URL("./web/mobile.js",import.meta.url),"utf8");
const markup=readFileSync(new URL("./web/index.html",import.meta.url),"utf8");
const nodes=new Map(), listeners={}, intervals=new Map(), timeouts=new Map(), network=[], tracks=[];
let now=0, seq=0, pendingGrant=null;
function element(){
  return {textContent:"",disabled:false,checked:false,hidden:false,dataset:{},append(){},replaceChildren(){},
    getContext(){return {drawImage(){},getImageData(){return {data:[45,45,45,255]};}};},async play(){},pause(){}};
}
for(const m of markup.matchAll(/\bid="([^"]+)"/g)) nodes.set(m[1],element());
const document={hidden:false, getElementById(id){assert(nodes.has(id),id);return nodes.get(id);},
  createElement:element,createTextNode:s=>s,querySelectorAll:()=>[],addEventListener:(e,f)=>listeners[e]=f};
function stream(){
  const track={stopped:false,stop(){this.stopped=true;}}; tracks.push(track);
  return {getTracks:()=>[track]};
}
class AudioContext {
  createAnalyser(){return {getFloatTimeDomainData:b=>b.fill(.02)};}
  createMediaStreamSource(){return {connect(){}};}
  async resume(){} async close(){}
}
const window={isSecureContext:true,addEventListener:(e,f)=>listeners[e]=f};
const ctx=vm.createContext({document,window,AudioContext,Float32Array,crypto:{randomUUID},performance:{now:()=>now},
  navigator:{mediaDevices:{getUserMedia:async()=>pendingGrant?await pendingGrant:stream()}},
  setInterval:f=>(intervals.set(++seq,f),seq),clearInterval:id=>intervals.delete(id),
  setTimeout:(f,d)=>(timeouts.set(++seq,{f,due:now+d}),seq),clearTimeout:id=>timeouts.delete(id),
  fetch:async(path,options)=>{
    const data=JSON.parse(options.body);network.push([path,data]);
    return {ok:true,json:async()=>path==="/api/pair"?{key:"test"}:{records:[],prediction:{}}};
  }});
vm.runInContext(ui,ctx);
async function tick(ms){
  for(let i=0;i<ms;i+=100){
    now+=100;
    for(const [id,t] of [...timeouts])if(t.due<=now){timeouts.delete(id);t.f();}
    for(const f of [...intervals.values()])await f();
  }
}
const n=id=>nodes.get(id);
await n("sound").onclick();await tick(1700);
assert.equal(n("draft").textContent,"sound · moderate");assert(tracks.at(-1).stopped);
await n("survey").onclick();await tick(8200);assert.equal(n("draft").textContent,"dynamics · steady");assert(tracks.at(-1).stopped);
await n("light").onclick();assert.equal(n("draft").textContent,"light · dim");
assert(tracks.at(-1).stopped);assert.deepEqual(network,[]);
n("code").value="test";await n("pairform").onsubmit({preventDefault(){}});
await n("save").onclick();
let saves=()=>network.filter(([path])=>path==="/api/save").map(([,body])=>body);
assert.equal(saves()[0].learn,false);
await n("listen").onclick();await tick(64000);assert.equal(saves().length,1);n("stop").onclick();
n("auto").checked=true;await n("listen").onclick();await tick(64000);
assert.equal(saves().length,2);assert.equal(saves()[1].learn,true);
assert.deepEqual(Object.keys(saves()[1]).sort(),["channel","id","learn","value"]);
n("stop").onclick();await tick(65000);assert.equal(saves().length,2);assert(!n("auto").checked);
await n("listen").onclick();await tick(301000);assert(tracks.at(-1).stopped);
let grant;pendingGrant=new Promise(r=>grant=r);
const acquisition=n("sound").onclick();document.hidden=true;listeners.visibilitychange();
const late=stream();grant(late);await acquisition;assert(late.getTracks()[0].stopped);assert.equal(intervals.size,0);
assert.equal(n("draft").textContent,"No observation waiting.");
pendingGrant=null;document.hidden=false;await n("listen").onclick();tracks.at(-1).onmute();
assert(tracks.at(-1).stopped);
const before=network.length;
await n("speak").onclick();assert.match(n("message").textContent,/unavailable/);
let recognition,availability="available";
class Recognizer {
  static async available(options){assert.equal(options.processLocally,true);return availability;}
  get processLocally(){return this.local;} set processLocally(x){this.local=x;}
  start(){assert(this.local); recognition=this;} abort(){this.aborted=true;}
}
window.SpeechRecognition=Recognizer;
availability="downloadable";await n("speak").onclick();assert.equal(recognition,undefined);
availability="available";await n("speak").onclick();
function result(s){return {results:[Object.assign([{transcript:s}],{isFinal:true})]};}
recognition.onresult(result("Walk with Jane."));assert.match(n("draft").textContent,/No observation/);
await n("speak").onclick();recognition.onresult(result("Walk."));assert.equal(n("draft").textContent,"activity · walk");
assert.equal(n("learn").checked,false);
await n("speak").onclick();const lateResult=recognition.onresult;n("stop").onclick();lateResult(result("rest"));assert.match(n("draft").textContent,/No observation/);
assert.equal(network.length,before);
console.log("Passed local-only speech feature gate, unavailable pack, third-party name rejection, approved label review and late-result suppression.");
console.log("Passed synthetic sound/light, local-only default, reviewed share, opt-in automatic share, session deadline, stop, hidden pending permission, route mute and wire schema.");
