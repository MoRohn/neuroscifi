import {checkFeatures,deviceReceipt,forecast,texture,outcomes,liveRows,vaultKey,newSalt,seal,openBox} from "./field-model.mjs";
const $=id=>document.getElementById(id), msg=t=>$("fieldmsg").textContent=t;
let linkedEvidence=[],managedPair=false,pendingId=null, context={}, rows=[], prediction=null, measured=null, vault=null, epoch=0, busy=false, imageGeneration=0;
const storageKey="neuroscifi.field.v1";
const labels={goal:"Goal",sound:"Sound",dynamics:"Sound variation",visible:"Pocket on screen",layout:"Screen detail",switches:"App changes",wakes:"Screen activations",screenTime:"Observed screen minutes",appTime:"Observed selected-app minutes",power:"Power mode",thermal:"Thermal state"};
function report(e){msg(e.message||"Action unavailable.");}
function describe(features){return Object.entries(features).map(([k,[v,s]])=>labels[k]+" · "+v+" ("+s+")").join(" / ");}
function paint(){
 rows=liveRows(rows);
 $("featurelist").textContent=describe(context)||"No signals selected.";
 $("guess").textContent=prediction?(prediction.answer?"Estimate: "+prediction.answer:"Insufficient evidence for a prediction")+
   " · "+prediction.ids.length+" similar learning episodes.":"";
 $("why").hidden=!prediction;
 if(prediction){
   const dist=Object.entries(prediction.probs).map(([k,v])=>k+" "+Math.round(v*100)+"%").join(", ");
   const evidence=prediction.ids.slice(0,3).map(id=>[...rows,...linkedEvidence].find(r=>r.id===id)).filter(Boolean);
   $("reason").textContent=dist+". "+(evidence.length?evidence.map(r=>describe(r.features)+" → "+r.outcome).join("; "):"No matching evidence available in this view.");
 }
 $("feedback").hidden=!prediction; $("keepweek").disabled=!vault;
 $("predict").disabled=!Object.keys(context).length || !!prediction;
 $("historyLocal").replaceChildren();
 for(const r of [...rows].reverse().filter(r=>r.outcome)){
   const li=document.createElement("li"), block=document.createElement("div");
   block.textContent=(r.features.goal?.[0]||"Episode")+" → "+r.outcome;
   const detail=document.createElement("small");detail.textContent=describe(r.features)+(r.learn?" · Learning enabled":" · Memory only")+(r.keep==="week"?" · 7 days locally":" · 24 hours");block.append(detail);
   const forget=document.createElement("button");forget.textContent="Forget here";forget.onclick=()=>mutate(list=>list.filter(x=>x.id!==r.id && !(x.pending?.ids.includes(r.id)))).then(()=>{if(prediction?.ids.includes(r.id))reset();}).catch(report);
   const send=document.createElement("button");send.textContent="Share episode";send.onclick=async()=>{
     if(!confirm("Share these categories, outcome and learning choice with the paired Spark for up to 24 hours?"))return;
     try{await window.pocketBridge("episode",{id:r.id,features:r.features,outcome:r.outcome,learn:r.learn});
       msg("Spark received episode. Manage its copy under Your Spark.");await window.pocketHistory();}catch{msg("Sharing unconfirmed. Pair or reconnect, then retry the same episode.");}
   };
   li.append(block,forget,send);$("historyLocal").append(li);
 }
 $("vaultstatus").textContent=vault?"Vault unlocked":"Session memory only";
}
function include(patch){if(prediction){msg("Finish or reset the current prediction first.");return;}Object.assign(context,checkFeatures(patch));paint();}
function reset(){linkedEvidence=[];pendingId=null;$("withSpark").checked=false;context={};prediction=null;measured=null;imageGeneration++;$("goal").value="";$("timer").textContent="Time Pocket on screen";$("locallearn").checked=false;$("keepweek").checked=false;paint();}
function lock(){epoch++;if(managedPair)window.pocketSetPair("");managedPair=false;vault=null;rows=[];$("pass").value="";reset();msg("Private workspace cleared from this view.");}
async function mutate(change){
 if(busy)throw Error("Finish the current update first.");
 busy=true; const ticket=epoch,session=vault;
 try{
   if(!session){const next=change(liveRows(rows));if(next.length>64)throw Error("Forget an episode first (64 limit).");rows=next;paint();return;}
   await navigator.locks.request(storageKey,async()=>{
     const raw=localStorage.getItem(storageKey);
     if(!raw)throw Error("Vault removed. Lock and reopen.");
     const box=JSON.parse(raw);if(box.salt!==session.salt)throw Error("Vault changed. Lock and reopen.");
     const next=change(await openBox(box,session.key));
     if(next.length>64)throw Error("Forget an episode first (64 limit).");
     const payload={salt:box.salt,auth:box.auth,...await seal(next,session.key)};
     if(epoch!==ticket) return;
     localStorage.setItem(storageKey,JSON.stringify(payload));rows=next;paint();
   });
 }finally{busy=false;}
}
$("unlock").onclick=async()=>{
 if(busy || vault)return;
 if(rows.length){msg("Open a vault before creating episodes, or clear the session first.");return;}
 if(!navigator.locks || !crypto.subtle){msg("Private vault unavailable on this browser.");return;}
 const pass=$("pass").value;$("pass").value="";
 if(pass.length<12){msg("Enter a passphrase of at least 12 characters.");return;}
 const ticket=epoch;busy=true;
 try{await navigator.locks.request(storageKey,async()=>{
   const raw=localStorage.getItem(storageKey),box=raw?JSON.parse(raw):null,salt=box?.salt||newSalt();
   const key=await vaultKey(pass,salt), next=box?await openBox(box,key):[];
   const auth=box?.auth?(await openBox(box.auth,key))[0]:null;
   const updated={salt,auth:auth?box.auth:undefined,...await seal(next,key)};
   if(epoch!==ticket)return;
   localStorage.setItem(storageKey,JSON.stringify(updated));vault={key,salt};rows=next;reset();
   if(auth){window.pocketSetPair(auth.credential);managedPair=true;}
   const pending=next.find(r=>r.pending);
   if(pending){pendingId=pending.id;context=pending.features;prediction=pending.pending;$("goal").value=context.goal?.[0]||"";}
   paint();msg(pending?"Prediction restored. Record what happened next.":"Vault open. Default retention is 24 hours.");
 });}catch{msg("Could not unlock or save the vault.");}finally{busy=false;}
};
$("rememberpair").onclick=async()=>{
 if(!vault||!window.pocketToken()){msg("Open a vault and pair with your Spark first.");return;}
 const ticket=epoch,session=vault,credential=window.pocketToken();
 try{await navigator.locks.request(storageKey,async()=>{
   const box=JSON.parse(localStorage.getItem(storageKey));
   if(!box || box.salt!==session.salt)throw Error("Vault changed.");
   const auth=await seal([{created:Date.now(),credential}],session.key);
   if(ticket!==epoch)return;
   localStorage.setItem(storageKey,JSON.stringify({...box,auth}));managedPair=true;msg("Pairing protected by this vault for 24 hours.");
 });}catch(e){report(e);}
};
$("lock").onclick=lock;
$("wipe").onclick=async()=>{
 if(!navigator.locks || !confirm("Erase this browser's encrypted vault and session memory? Spark copies have separate controls."))return;
 lock();
 await navigator.locks.request(storageKey,()=>localStorage.removeItem(storageKey));msg("Local vault erased.");
};
window.addEventListener("storage",e=>{if(e.key===storageKey && vault)lock();});
$("goal").onchange=e=>{if(e.target.value)include({goal:[e.target.value,"owner"]});else if(!prediction){delete context.goal;paint();}};
$("useSound").onclick=()=>{const v=window.pocketSound();if(v)include(v);else msg("Use Check sound first, then include its reviewed level.");};
$("timer").onclick=()=>{
 if(prediction)return;
 if(measured===null){measured=performance.now();$("timer").textContent="Finish on-screen sample";return;}
 const seconds=(performance.now()-measured)/1000;measured=null;
 $("timer").textContent="Time Pocket on screen";include({visible:[seconds<60?"brief":seconds<300?"several":"extended","pocket"]});
};
$("screenfile").onchange=async e=>{
 const file=e.target.files[0];e.target.value="";if(!file||prediction)return;
 const ticket=++imageGeneration;
 let bitmap;
 try{
   if(!["image/png","image/jpeg","image/webp"].includes(file.type)||file.size>8e6)throw Error("Choose a PNG, JPEG or WebP under 8 MB.");
   bitmap=await createImageBitmap(file,{resizeWidth:32,resizeHeight:32});
   if(ticket!==imageGeneration||document.hidden)return;
   const c=document.createElement("canvas"); c.width=c.height=32;const g=c.getContext("2d");
   g.drawImage(bitmap,0,0,32,32);const pixels=g.getImageData(0,0,32,32).data;
   include({layout:[texture(pixels),"image"]});pixels.fill(0);c.width=0;
 }catch{msg("Image unavailable. Other evidence can still be used.");}finally{bitmap?.close();}
};
$("nativefile").onchange=async e=>{
 const file=e.target.files[0];e.target.value="";if(!file||prediction)return;
 const ticket=epoch;
 try{if(file.size>1024)throw Error();const data=deviceReceipt(JSON.parse(await file.text()));
 if(ticket===epoch&&!document.hidden)include(data);}catch{msg("Receipt rejected. Choose the categorical export from Pocket Metrics.");}
};
$("predict").onclick=async()=>{
 if(!Object.keys(context).length || prediction || busy)return;
 const ticket=epoch, features=structuredClone(context), useSpark=$("withSpark").checked;
 try{
   let all=liveRows(rows);
   if(useSpark){
     const response=await window.pocketBridge("episodes",{});
     const known=new Set(all.map(x=>x.id));
     linkedEvidence=response.episodes.filter(r=>!known.has(r.id)).map(r=>({...r,created:0}));
     all=[...all,...linkedEvidence];
   }
   if(ticket!==epoch || prediction)return;
   const result=forecast(all,features), id=crypto.randomUUID();
   const pending={id,created:Date.now(),features,outcome:null,learn:false,pending:result};
   await mutate(list=>{
     if(list.some(r=>r.pending))throw Error("A prediction is already pending. Reopen the vault to resume.");
     return [...list,pending];
   });
   if(ticket!==epoch)return;
   context=features;pendingId=id;prediction=result;paint();
 }catch(e){report(e);}
};
$("resetfield").onclick=async()=>{try{if(pendingId) await mutate(a=>a.filter(r=>r.id!==pendingId));reset();}catch(e){report(e);}};
document.querySelectorAll("[data-outcome]").forEach(b=>b.onclick=async()=>{
 if(!prediction || busy)return;
 const record={id:pendingId,created:Date.now(),features:structuredClone(context),keep:vault && $("keepweek").checked?"week":"day",outcome:b.dataset.outcome,learn:$("locallearn").checked};
 if(!outcomes.includes(record.outcome))return;
 try{await mutate(list=>{
   if(!list.some(r=>r.id===record.id && r.pending))throw Error("Prediction expired or was erased. Reset to start again.");
   return list.map(r=>r.id===record.id?record:r);
 });reset();msg("Outcome recorded on this device.");}catch(e){report(e);}
});
document.addEventListener("visibilitychange",()=>{if(document.hidden)lock();});
window.addEventListener("pagehide",lock);
setInterval(()=>{if(!document.hidden)paint();},60000);
if("serviceWorker" in navigator && window.isSecureContext){
 navigator.serviceWorker.register("/sw.js").then(()=>navigator.serviceWorker.ready).then(()=>{$("offlinestate").textContent="Offline shell ready";}).catch(()=>{});
}
paint();
