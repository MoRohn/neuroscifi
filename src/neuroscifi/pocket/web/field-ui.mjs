import {checkFeatures,deviceReceipt,forecast,texture,outcomes,liveRows,vaultKey,newSalt,seal,openBox} from "./field-model.mjs";
const $=id=>document.getElementById(id), msg=t=>$("fieldmsg").textContent=t;
let linkedEvidence=[],managedPair=false,pendingId=null, context={}, rows=[], prediction=null, measured=null, vault=null, epoch=0, busy=false, imageGeneration=0;
let recording=false, sharingEpisodes=new Set(), actedAt=-1e9;
const OUTCOME={continued:"Continued",switched:"Switched tasks",paused:"Paused"};
// After an outcome is recorded, controls drawn where the finger was ignore taps for a moment (P-01).
const settled=()=>performance.now()-actedAt>700;
const storageKey="neuroscifi.field.v1";
const labels={goal:"Goal",sound:"Sound",dynamics:"Sound variation",visible:"Pocket on screen",layout:"Screen detail",switches:"App changes",wakes:"Screen activations",screenTime:"Observed screen minutes",appTime:"Observed selected-app minutes",power:"Power mode",thermal:"Thermal state"};
function report(e){msg(e.message||"Action unavailable.");}
function describe(features){return Object.entries(features).map(([k,[v,s]])=>labels[k]+" · "+v+" ("+s+")").join(" / ");}
const episodes=n=>n===1?"1 similar learning episode":n+" similar learning episodes";
function paint(){
 rows=liveRows(rows);
 $("featurelist").textContent=describe(context)||"No signals selected.";
 // Abstention in plain words (UX 3): what Pocket found and what it needs before it guesses.
 $("guess").textContent=!prediction?"":prediction.answer?"Estimate: "+OUTCOME[prediction.answer]+" · "+episodes(prediction.ids.length)+"."
   :"Insufficient evidence for a prediction: "+(prediction.ids.length?episodes(prediction.ids.length)+" found":"no similar learning episodes yet")+
    ". Pocket guesses only when about three closely matching episodes mostly (55% or more) ended the same way.";
 $("why").hidden=!prediction;
 if(prediction){
   const dist=Object.entries(prediction.probs).map(([k,v])=>OUTCOME[k].toLowerCase()+" "+Math.round(v*100)+"%").join(", ");
   const evidence=prediction.ids.slice(0,3).map(id=>[...rows,...linkedEvidence].find(r=>r.id===id)).filter(Boolean);
   $("reason").textContent=(prediction.answer?"Chances from similar episodes: ":"Chances so far, too uncertain to name one: ")+dist+". "+
     (evidence.length?"Closest episodes: "+evidence.map(r=>describe(r.features)+" → "+r.outcome).join("; "):"No matching learning episodes on this device yet; each starts from an even split.");
 }
 $("feedback").hidden=!prediction; $("keepweek").disabled=!vault;
 $("predict").disabled=!Object.keys(context).length || !!prediction;
 $("historyLocal").replaceChildren();
 for(const r of [...rows].reverse().filter(r=>r.outcome)){
   const li=document.createElement("li"), block=document.createElement("div");
   block.textContent=(r.features.goal?.[0]||"Episode")+" → "+r.outcome;
   const detail=document.createElement("small");detail.textContent=describe(r.features)+(r.learn?" · Learning enabled":" · Memory only")+(r.keep==="week"?" · 7 days locally":" · 24 hours");block.append(detail);
   const name=(r.features.goal?.[0]||"episode")+" → "+r.outcome;
   const forget=document.createElement("button");forget.textContent="Forget here";forget.ariaLabel="Forget here: "+name;forget.onclick=()=>{
     if(!settled()||!confirm("Forget this episode ("+name+") on this device? This cannot be undone."))return;
     mutate(list=>list.filter(x=>x.id!==r.id && !(x.pending?.ids.includes(r.id)))).then(()=>{if(prediction?.ids.includes(r.id))reset();msg("Episode forgotten on this device.");$("fieldmsg").focus?.();}).catch(report);
   };
   const send=document.createElement("button");send.textContent="Share episode";send.ariaLabel="Share episode: "+name;send.onclick=async()=>{
     if(sharingEpisodes.has(r.id))return;  // one dialog and one POST per episode while its request is in flight (UX 10, RT-B-01)
     sharingEpisodes.add(r.id);
     try{
       if(!confirm("Share these categories, outcome and learning choice with the paired Spark for up to 24 hours?"))return;
       await window.pocketBridge("episode",{id:r.id,features:r.features,outcome:r.outcome,learn:r.learn});
       msg("Spark received episode. Manage its copy under Your Spark.");await window.pocketHistory();
     }catch(e){if(!e.revoked)msg(e.status===0?"Your Spark is unreachable. Nothing was shared; retry the same episode when it is back.":"Sharing unconfirmed. Pair or reconnect, then retry the same episode.");}
     finally{sharingEpisodes.delete(r.id);}
   };
   li.append(block,forget,send);$("historyLocal").append(li);
 }
 $("vaultstatus").textContent=vault?"Vault unlocked":"Session memory only";
 document.dispatchEvent?.(new Event("pocketvault"));
}
function include(patch){if(prediction){msg("Finish or reset the current prediction first.");return false;}release();Object.assign(context,checkFeatures(patch));paint();return true;}
// Keep the field session's height while its outcome buttons collapse, so nothing moves under the pointer (P-01).
function reserve(){const ws=$("workspace"),h=ws.getBoundingClientRect?.().height;if(h&&ws.style)ws.style.minHeight=h+"px";}
function release(){const ws=$("workspace");if(ws.style)ws.style.minHeight="";}
function reset(){linkedEvidence=[];pendingId=null;$("withSpark").checked=false;context={};prediction=null;measured=null;imageGeneration++;$("goal").value="";$("timer").textContent="Time Pocket on screen";$("locallearn").checked=false;$("keepweek").checked=false;paint();}
function lock(){epoch++;if(managedPair)window.pocketSetPair("");managedPair=false;vault=null;rows=[];$("pass").value="";release();reset();msg("Private workspace cleared from this view.");}
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
// Saved research findings live in the same vault: same AES-GCM key, 64-entry limit and 24-hour expiry.
export const vaultOpen=()=>!!vault;
export const findings=()=>liveRows(rows).filter(r=>r.research);
export async function keepFinding(entry){
 if(!vault)throw Error("Open your device vault first (Device memory). Nothing was stored.");
 await mutate(list=>{
   const rest=list.filter(r=>r.id!==entry.id);
   if(rest.filter(r=>r.research).length>=8)throw Error("Forget a saved finding first (8 limit).");
   return [...rest,entry];
 });
}
export const forgetFinding=id=>mutate(list=>list.filter(r=>r.id!==id));
// Remove the vault-held pairing (Forget this pairing, or a Spark that refused it). Needs no passphrase: only the sealed
// `auth` field is dropped from the stored box (P-02).
async function dropPair(){
 managedPair=false;
 if(!navigator.locks)return;
 await navigator.locks.request(storageKey,()=>{
   const raw=localStorage.getItem(storageKey);if(!raw)return;
   const box=JSON.parse(raw);if(!box.auth)return;
   delete box.auth;localStorage.setItem(storageKey,JSON.stringify(box));
 });
}
window.pocketDropPair=()=>dropPair().catch(()=>{});
$("unlock").onclick=async()=>{
 if(busy || vault)return;
 if(rows.length){msg("This session already has episodes outside a vault. Choose “Lock and clear view” to discard them, then open the vault.");return;}
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
   const pending=next.find(r=>r.pending);
   if(pending){pendingId=pending.id;context=pending.features;prediction=pending.pending;$("goal").value=context.goal?.[0]||"";}
   paint();msg(pending?"Prediction restored. Record what happened next.":"Vault open. Default retention is 24 hours.");
   if(auth){managedPair=true;window.pocketSetPair(auth.credential,auth.spark);}
 });}catch(e){
   // AES-GCM refuses a wrong key with OperationError; anything else is storage (UX 7).
   msg(e?.name==="OperationError"?"That passphrase does not open the vault on this device. Check it and try again. Forgot it? “Erase local vault” starts a new one."
     :"Could not open or save the vault: this browser's storage is unavailable or full.");
 }finally{busy=false;}
};
$("rememberpair").onclick=async()=>{
 if(!vault||!window.pocketToken()){msg("Open a vault and pair with your Spark first.");return;}
 const ticket=epoch,session=vault,credential=window.pocketToken();
 try{await navigator.locks.request(storageKey,async()=>{
   const box=JSON.parse(localStorage.getItem(storageKey));
   if(!box || box.salt!==session.salt)throw Error("Vault changed.");
   const auth=await seal([{created:Date.now(),credential,spark:window.pocketSpark?.()||null}],session.key);  // the Spark pin goes with it
   if(ticket!==epoch)return;
   localStorage.setItem(storageKey,JSON.stringify({...box,auth}));managedPair=true;msg("Pairing protected by this vault for 24 hours.");
 });}catch(e){report(e);}
};
$("lock").onclick=lock;
$("wipe").onclick=async()=>{
 if(!navigator.locks)return;
 if(localStorage.getItem(storageKey)===null){msg("No local vault to erase.");return;}
 if(!confirm("Erase this browser's encrypted vault and session memory? Spark copies have separate controls."))return;
 lock();
 await navigator.locks.request(storageKey,()=>localStorage.removeItem(storageKey));msg("Local vault erased.");
};
window.addEventListener("storage",e=>{if(e.key===storageKey && vault)lock();});
// A refused change puts the select back, so it never shows a goal the prediction does not use (P-06).
$("goal").onchange=e=>{
 if(e.target.value){if(!include({goal:[e.target.value,"owner"]}))e.target.value=context.goal?.[0]||"";}
 else if(prediction){msg("Finish or reset the current prediction first.");e.target.value=context.goal?.[0]||"";}
 else{release();delete context.goal;paint();}
};
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
   if(!["image/png","image/jpeg","image/webp"].includes(file.type))throw Error("That file is "+(file.type||"not an image type this page knows")+". Choose a PNG, JPEG or WebP screenshot.");
   if(file.size>8e6)throw Error("That image is "+(file.size/1e6).toFixed(1)+" MB. Choose one under 8 MB.");
   try{bitmap=await createImageBitmap(file,{resizeWidth:32,resizeHeight:32});}
   catch{throw Error("This browser could not read that image; the file may be damaged. Try another screenshot.");}
   if(ticket!==imageGeneration||document.hidden)return;
   const c=document.createElement("canvas"); c.width=c.height=32;const g=c.getContext("2d");
   g.drawImage(bitmap,0,0,32,32);const pixels=g.getImageData(0,0,32,32).data;
   include({layout:[texture(pixels),"image"]});pixels.fill(0);c.width=0;
 }catch(err){msg((err?.message||"Image unavailable.")+" Other evidence can still be used.");}finally{bitmap?.close();}
};
$("nativefile").onchange=async e=>{
 const file=e.target.files[0];e.target.value="";if(!file||prediction)return;
 const ticket=epoch;
 try{if(file.size>1024)throw Error();const data=deviceReceipt(JSON.parse(await file.text()));
 if(ticket===epoch&&!document.hidden)include(data);}catch{msg("Receipt rejected. Choose the categorical export from Pocket Metrics.");}
};
$("predict").onclick=async()=>{
 if(!Object.keys(context).length || prediction || busy)return;
 release();
 const ticket=epoch, features=structuredClone(context), useSpark=$("withSpark").checked;
 try{
   let all=liveRows(rows);
   if(useSpark && !window.pocketToken()){msg("Pair with your Spark first, or turn off linked devices.");return;}
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
$("resetfield").onclick=async()=>{try{if(pendingId) await mutate(a=>a.filter(r=>r.id!==pendingId));release();reset();}catch(e){report(e);}};
const outcomeButtons=[...document.querySelectorAll("[data-outcome]")];
outcomeButtons.forEach(b=>b.onclick=async()=>{
 if(!prediction || busy || recording)return;  // re-entrancy guard: one outcome per prediction
 const record={id:pendingId,created:Date.now(),features:structuredClone(context),keep:vault && $("keepweek").checked?"week":"day",outcome:b.dataset.outcome,learn:$("locallearn").checked};
 if(!outcomes.includes(record.outcome))return;
 recording=true;outcomeButtons.forEach(x=>x.disabled=true);
 try{await mutate(list=>{
   if(!list.some(r=>r.id===record.id && r.pending))throw Error("Prediction expired or was erased. Reset to start again.");
   return list.map(r=>r.id===record.id?record:r);
 });actedAt=performance.now();reserve();reset();msg("Outcome recorded on this device: "+OUTCOME[record.outcome]+".");$("fieldmsg").focus?.();}
 catch(e){report(e);}
 finally{recording=false;outcomeButtons.forEach(x=>x.disabled=false);}
});
document.addEventListener("visibilitychange",()=>{if(document.hidden)lock();});
window.addEventListener("pagehide",lock);
setInterval(()=>{if(!document.hidden)paint();},60000);
let shellReady=false;
const shellText=()=>{$("offlinestate").textContent=(shellReady?"Offline shell ready":"Online shell")+(navigator.onLine===false?" · no network now":"");};
window.addEventListener("online",shellText);window.addEventListener("offline",shellText);
if("serviceWorker" in navigator && window.isSecureContext){
 navigator.serviceWorker.register("/sw.js").then(()=>navigator.serviceWorker.ready).then(()=>{shellReady=true;shellText();}).catch(()=>{});
}
shellText();
paint();
