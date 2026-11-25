// Research from Pocket (ADR-028): queue a question for the Spark, follow its stages, read and save the report.
// Pocket never researches itself. Server text is rendered with textContent only; links are http(s) only.
// Shared links prefill the question for review (never submitted for you); Read aloud uses an on-device voice only;
// notifications are opt-in: from this open page (no push service), or per device through an empty Web Push (ADR-040);
// see mobile/README.md.
import {vaultOpen,findings,keepFinding,forgetFinding,vaultOutbox,changeOutbox} from "./field-ui.mjs";
import {liveRows} from "./field-model.mjs";
import * as nodeKey from "./node-key.mjs";
import * as outbox from "./outbox.mjs";
const $=id=>document.getElementById(id);
const TERMINAL=["completed","partial","failed","cancelled"], STAGES=["discovering","reading","evaluating","synthesizing"];
// Labels shared with the console (U-21): stages and statuses as the console's TASK_LABELS, kinds as #researchmode.
const LABEL={queued:"Queued",discovering:"Discovering",reading:"Reading",evaluating:"Evaluating",synthesizing:"Synthesizing",
  completed:"Completed",partial:"Partial",failed:"Failed",cancelled:"Cancelled"};
const STATUS={corroborated:"Corroborated",single_source:"Single source",contested:"Contested",insufficient:"Insufficient evidence"};
const VERDICT={supported:"Supported",disputed:"Disputed",contested:"Contested",insufficient:"Not enough evidence"};
const MAX=300, MAX_BYTES=1024, MAX_SAVED_BYTES=64000, SUMMARIZE="Summarize: ";
let research=false, paired=false, tasks=[], current=null, timer=0, generation=0, busy=false, dictation=null, savedSignature="";
let workerRunning=null;   // research_list worker_running: false says plainly that no research worker runs (R6 setup h)
const NO_WORKER="Your Spark has no research worker running";
// R6 setup (f): the list itself is refreshed every LIST_MS while the page is visible, so a question asked on another
// linked device appears without pressing anything; a hidden page checks nothing.
const LIST_MS=10000;
let listTimer=0;
let sharing=false, notify=false, away=null, reading=null, session=null, shown=null;
let offered=false, push=null, pushOn=false, pushBusy=false, pushOld=false, pushTesting=false;  // push: the Spark's status
// This device as a research node (ADR-042 draft): the Spark's view (node_enrol status) and this browser's own key.
let nodeInfo=null, localKey=null, nodeBusy=false;
// The outbox (outbox.mjs): questions waiting for the Spark. In the vault while it is open, else here (this tab only).
let tabOutbox=[], draining=false, outboxTimer=0, outboxTries=0, moving=false;
// Why sending stopped: "network" (out of reach: any reply from the Spark is the moment to send again) or "spark"
// (the Spark said not now: wait for the backoff, or for Send now).
let heldBy=null;
const say=t=>{$("researchmsg").textContent=t;};
const active=t=>!TERMINAL.includes(t.status);

// R2-05: a question with personal details is refused here, before anything is sent, as the Spark would refuse it.
// The rules are webcortex/safety.py _PRIVACY_PATTERNS whose rule is the pattern alone, copied verbatim
// (tests/test_pocket_research.py keeps them identical); kinds that need a check beyond the pattern (card, IBAN,
// phone, IP address, secret) and the owner's private terms stay with the Spark, which still screens every question.
const PRIVATE_RULES=[
  ["api_key",String.raw`(?<![\w-])(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}|sk_(?:live|test)_[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|gh[ousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}|AKIA[0-9A-Z]{16}|xox[bpars]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,})`,""],
  ["jwt",String.raw`(?<![\w-])eyJ[A-Za-z0-9_-]{4,}\.eyJ[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]*`,""],
  ["email",String.raw`(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b`,""],
  ["email",String.raw`(?<![\w.+-])[A-Za-z0-9_%+-]+(?:(?:\.|\s*[(\[{<]\s*dot\s*[)\]}>]\s*|\s+dot\s+)[A-Za-z0-9_%+-]+)*(?:\s*[(\[{<]\s*at\s*[)\]}>]\s*|\s+at\s+)[A-Za-z0-9-]+(?:(?:\.|\s*[(\[{<]\s*dot\s*[)\]}>]\s*|\s+dot\s+)[A-Za-z0-9-]+)*(?:\.|\s*[(\[{<]\s*dot\s*[)\]}>]\s*|\s+dot\s+)[A-Za-z]{2,}\b`,"i"],
  ["mac_address",String.raw`(?<![\w:-])[0-9A-Fa-f]{2}(?:([:-])[0-9A-Fa-f]{2})(?:\1[0-9A-Fa-f]{2}){4}(?![\w:-])`,""],
  ["file_path",String.raw`(?<![\w:/.~-])(?:/(?:home|Users|root|etc|var|tmp|opt|srv|mnt|media|private|usr|run|proc|data)/[^\s'\"<>]*|~/[^\s'\"<>]+|[A-Za-z]:\\[^\s'\"<>]*|\\\\[\w.-]+\\[^\s'\"<>]+)`,""],
].map(([kind,source,flags])=>[kind,new RegExp(source,flags)]);
const PRIVATE_WORDS={api_key:"an API key",jwt:"a login token",email:"an e-mail address",mac_address:"a device (MAC) address",file_path:"a file path"};
export function privateKinds(text){return [...new Set(PRIVATE_RULES.filter(([,rx])=>rx.test(text)).map(([kind])=>kind))];}
// Every research reply must carry the Spark's signature over its status, route, this request's nonce and body, under
// the key pinned at pairing; an unverified success is refused and an unverified error says nothing beyond its status.
// A node's state-changing requests are signed with its own key (ADR-042 draft; RESEARCH_POLICY.md section 6).
const SIGNED=new Set(["research_submit","research_continue","research_cancel","research_session"]);
const NODE_REFUSALS=new Set(["signature_required","signature_invalid","stale_request","replayed_request","malformed","node_revoked","unknown_node","reenrol","wrong_family"]);
const signs=(route,data)=>SIGNED.has(route)&&!(route==="research_session"&&(data.action||"status")==="status")&&nodeInfo?.state==="enrolled"&&nodeInfo.mine;
async function call(route,data,retry=true){
  const token=window.pocketToken?.(), pin=window.pocketSpark?.();
  if(!token)throw Object.assign(Error("Not paired"),{status:0,code:"not_paired"});  // local state, not a Spark refusal
  if(!pin)throw Object.assign(Error("No Spark key pinned"),{status:0,code:"no_pin"});
  const text=JSON.stringify(data), bytes=new TextEncoder().encode(text), nonce=nodeKey.nonce(), path="/api/"+route;
  const headers={"Content-Type":"application/json",Authorization:"Bearer "+token,"X-NSF-Nonce":nonce};
  if(signs(route,data))Object.assign(headers,await nodeKey.signedHeaders(nodeInfo,path,bytes,nonce));
  let response;
  try{response=await fetch(path,{method:"POST",headers,body:text,cache:"no-store",credentials:"omit"});}
  catch{if(window.pocketToken?.()===token)window.pocketReach?.(false);throw Object.assign(Error("Spark unreachable"),{status:0});}
  if(window.pocketToken?.()===token){if(response.status===403)window.pocketRefused?.();else window.pocketReach?.(true);}
  const raw=new Uint8Array(await response.arrayBuffer().catch(()=>new ArrayBuffer(0)));
  const genuine=await nodeKey.verifySpark(pin,response.status,path,nonce,raw,response.headers.get("X-NSF-Spark-Signature")).catch(()=>false);
  let body={};
  if(genuine)try{body=JSON.parse(new TextDecoder().decode(raw)||"{}");}catch{body={};}
  if(response.ok&&!genuine)throw Object.assign(Error("Unverified reply"),{status:response.status,code:"unverified"});
  if(!response.ok){
    if(NODE_REFUSALS.has(body.error)&&route!=="node_enrol"){
      await loadNode();   // the Spark's view of this node changed (enrolled, revoked): follow it
      if(retry&&body.error==="signature_required"&&signs(route,data))return call(route,data,false);
    }
    throw Object.assign(Error("Spark returned "+response.status),{status:response.status,code:body.error,body});
  }
  return body;
}
function fail(e){
  if(e.code==="not_paired")return;  // the pairing was dropped here; mobile.js already says why (RT-B-08 race)
  if(e.code==="owned"&&e.body?.session&&current&&!current.saved)setSession(current.id,e.body);
  if(e.status===404){research=false;tasks=[];if(current&&!current.saved)close();layout();say("Research is not available from this Spark.");return;}
  if(e.code==="private_data"){  // like the console: keep focus on the field and tie the refusal to it
    const f=$("objective");f.setAttribute("aria-invalid","true");f.setAttribute("aria-describedby","objectivecount researchmsg");
    queueMicrotask(()=>f.focus());
  }
  say(e.code==="unverified"?"Pocket could not confirm that this reply came from your Spark, so nothing from it is shown. Check that this device is on your own network, or pair again."
    :e.code==="no_pin"?"This pairing does not know your Spark's signing key, so Pocket cannot check its answers. Forget this pairing and pair again with a new code."
    :NODE_TEXT[e.code]?NODE_TEXT[e.code]
    :e.code==="daily_budget"?"Your Spark's daily research budget is used up. Nothing was queued. It renews tomorrow, or the owner can raise it."
    :e.code==="owned"?"Another of your devices is driving this research right now. Ask it to hand it over."
    :e.code==="not_continuable"?"Only a cancelled, partial or failed question can be continued."
    :e.status===429?"Limit reached: up to 10 questions per hour from your linked devices together. Try again later."
    :e.code==="private_data"?"Refused by your Spark: the question contains personal details such as an e-mail address, a phone number or a name you marked private. Nothing was stored or searched. Remove personal details and submit again."
    :e.status===507?"Your Spark has paused new research because its storage needs attention. Nothing was queued. Try again after the owner frees space."
    :e.status===400?"Your Spark rejected that request. Keep the question within 300 characters and 1024 bytes."
    :e.status===403?"Your Spark no longer accepts this pairing. Pair again."
    :e.status===0?"Spark unreachable. Saved findings stay readable on this device."
    :"Your Spark could not do that right now (error "+e.status+"). Try again in a moment.");
}
const NODE_TEXT={
  signature_required:"Your Spark accepts this research node's changes only when signed, and this device could not sign. Check the research node section below.",
  signature_invalid:"Your Spark refused this device's signature. Ask the owner to enrol this device again.",
  replayed_request:"Your Spark refused a repeated request. Try again.",
  malformed:"Your Spark refused this device's signature. Ask the owner to enrol this device again.",
  stale_request:"Your Spark refused the request because this device's clock differs from the Spark's by more than 2 minutes. Correct the clock and try again.",
  node_revoked:"The owner revoked this device as a research node, and your Spark deleted its key. It can still ask and read research; ask the owner to enrol it again to drive research.",
  unknown_node:"Your Spark does not know this device's key. Ask the owner to enrol this device again.",
  wrong_family:"Your Spark refused the request: this device's key belongs to another pairing.",
  reenrol:"This device was enrolled as a research node before your Spark required signed requests. Enrol it again under “This device as a research node”; the owner confirms the fingerprint on the Spark console."};
function node(tag,text,cls){const e=document.createElement(tag);if(text!=null)e.textContent=text;if(cls)e.className=cls;return e;}
function safeUrl(u){try{const x=new URL(u);return x.protocol==="http:"||x.protocol==="https:"?x.href:null;}catch{return null;}}
const pct=c=>typeof c==="number"&&isFinite(c)&&c>=0&&c<=1?Math.round(c*100)+"% confidence":null;
const day=ts=>typeof ts==="number"&&isFinite(ts)?new Date(ts*1000).toLocaleDateString([], {year:"numeric",month:"short",day:"numeric"}):null;
function stageText(t){
  if(t.cancel_requested&&active(t))return "Cancelling…";
  if(t.status==="queued")return workerRunning===false?"Queued · "+NO_WORKER:"Queued · waiting for your Spark's research worker";
  const i=STAGES.indexOf(t.status), s=t.spent||{};
  // incremental progress: what the Spark has spent on it so far (0.7.0 research_get.spent)
  const spent=typeof s.searches==="number"&&(s.searches||s.pages)?" · "+s.searches+" searches, "+(s.pages||0)+" pages so far":"";
  return i>=0?LABEL[t.status]+" · step "+(i+1)+" of 4"+spent:(LABEL[t.status]||String(t.status));
}
function layout(){
  const saved=vaultOpen()&&findings().length>0;
  $("research").hidden=!(research||saved||sharing||allWaiting().length); $("navresearch").hidden=$("research").hidden;
  $("researchform").hidden=!(research&&paired); $("researchtasks").hidden=!research;
  $("researchsavedbox").hidden=!saved;$("researchnode").hidden=!(research&&paired);
  $("researchhelp").textContent=research?"Your Spark researches the public web and reports with sources. Pocket only sends the question.":
    sharing?"Pair with a research-enabled Spark to summarize the shared link. Nothing is sent before you submit.":
    "Saved findings on this device. Pair with a research-enabled Spark to ask new questions.";
  buttons();
}
// The request is limited in bytes, not characters (S-08): count what will actually be sent.
function body(){const data={objective:$("objective").value.trim()};if($("researchmode").value)data.mode=$("researchmode").value;return data;}
const bytes=data=>new TextEncoder().encode(JSON.stringify(data)).length;
function buttons(){
  const text=$("objective").value, size=bytes(body()), chars=[...text].length;
  $("objectivecount").textContent=chars+" / "+MAX+" characters · "+size+" / "+MAX_BYTES+" bytes"+(size>MAX_BYTES?" · too long to send":"");
  $("researchsubmit").disabled=!paired||!research||busy||!text.trim()||chars>MAX||size>MAX_BYTES;
  $("researchsubmithint").textContent=!$("researchsubmit").disabled?(navigator.onLine===false?"This device is offline. Your question will wait here and be sent once your Spark answers.":""):!paired||!research?"Pair with a research-enabled Spark to submit.":
    busy?"Sending your question…":!text.trim()?"Type a question to enable Submit.":"Shorten the question to enable Submit.";
  $("dictate").disabled=!!dictation;
}
function drawList(){
  const list=$("researchlist");list.replaceChildren();
  for(const t of tasks){
    const li=node("li",null,"rtask"), text=node("div",t.objective);
    text.append(node("small",stageText(t)));
    const open=node("button","Open");open.ariaLabel="Open: "+t.objective;open.onclick=()=>openTask(t.id);
    li.append(text,open);list.append(li);
  }
  if(!tasks.length)list.append(node("li","No questions from your devices yet.","rtask"));
}
function drawSaved(){
  const rows=vaultOpen()?findings():[], signature=rows.map(r=>r.id+":"+r.created).join();
  if(current?.saved&&!rows.some(r=>r.id===current.id))close();
  if(signature===savedSignature){layout();return;}
  savedSignature=signature;
  const list=$("researchsaved");list.replaceChildren();
  for(const r of rows){
    const li=node("li",null,"rtask"), text=node("div",r.research.objective);
    text.append(node("small","Saved "+new Date(r.created).toLocaleString([], {hour:"numeric",minute:"2-digit",month:"short",day:"numeric"})+" · kept 24 hours"));
    const view=node("button","View");view.ariaLabel="View saved: "+r.research.objective;view.onclick=()=>{current={id:r.id,saved:true};show(r.research,true);$("researchview").focus();};
    const forget=node("button","Forget here");forget.ariaLabel="Forget saved: "+r.research.objective;
    forget.onclick=()=>forgetFinding(r.id).then(()=>say("Saved finding forgotten on this device.")).catch(e=>say(e.message));
    li.append(text,view,forget);list.append(li);
  }
  layout();
}
// ---- the outbox: questions waiting for your Spark --------------------------------------------------------------
const allWaiting=()=>[...(vaultOpen()?vaultOutbox():[]),...(tabOutbox=liveRows(tabOutbox))];
const when=ms=>new Date(ms).toLocaleString([], {hour:"numeric",minute:"2-digit",month:"short",day:"numeric"});
const keptUntil=ms=>new Date(ms).toLocaleDateString([], {month:"short",day:"numeric"});
// Apply a change to the waiting questions wherever they are kept (the vault, this tab, or both while moving).
async function changeWaiting(change){
  tabOutbox=change(tabOutbox);
  try{if(vaultOpen())await changeOutbox(change);}
  finally{drawOutbox();}
}
async function keepWaiting(item){
  if(outbox.full(allWaiting()))throw Error(outbox.MAX_WAITING+" questions are already waiting. Remove one first.");
  if(vaultOpen())await changeOutbox(list=>[...list,item]);
  // Not in the vault after all (it locked while saving): keep it in this tab rather than lose it.
  if(!vaultOutbox().some(r=>r.id===item.id))tabOutbox=[...tabOutbox,item];
  drawOutbox();
}
// Opening the vault takes in what waited in this tab, so it survives a reload from then on.
async function moveIntoVault(){
  if(moving||!vaultOpen()||!tabOutbox.length)return;
  moving=true;const taken=tabOutbox;
  try{await changeOutbox(list=>[...list,...taken.filter(t=>!list.some(r=>r.id===t.id))]);tabOutbox=tabOutbox.filter(t=>!taken.includes(t));}
  catch{/* the vault is busy or full: they stay in this tab and are still shown */}
  finally{moving=false;drawOutbox();}
}
function laterNote(e){
  return e.code==="unverified"?"Pocket could not confirm your Spark's reply. It asks again; a question is never queued twice."
    :e.code==="not_paired"||e.code==="no_pin"?"Pair with your Spark to send it."
    :e.status===0?"Your Spark is out of reach."
    :e.code==="daily_budget"?"Your Spark's daily research budget is used up. Pocket tries again later."
    :e.status===429?"Up to 10 questions an hour from your linked devices together. Pocket tries again later."
    :e.status===507?"Your Spark has paused new research until its storage is freed."
    :e.status===404?"Research from this device is off on your Spark."
    :e.status===403?"Your Spark no longer accepts this pairing. Pair again to send it."
    :NODE_TEXT[e.code]||"Your Spark could not take it right now (error "+e.status+"). Pocket tries again later.";
}
const refusedNote=e=>e.code==="private_data"?"Not sent: your Spark found personal details in it. Edit it to remove them, or remove it."
  :"Not sent: your Spark rejected it as written (at most 300 characters and 1024 bytes). Edit it or remove it.";
function drawOutbox(){
  const items=allWaiting().sort((a,b)=>a.created-b.created), pin=window.pocketSpark?.(), list=$("outboxlist");
  list.replaceChildren();
  for(const r of items){
    const q=r.outbox, li=node("li",null,"rtask"), text=node("div",q.objective);
    const elsewhere=!!pin&&q.spark!==pin.key;
    text.append(node("small",q.state==="refused"?q.note
      :(elsewhere?"Written for the Spark of an earlier pairing; Pocket sends it only to that Spark":"Waiting to send · written "+when(r.created))
       +" · kept on this device until "+keptUntil(outbox.until(r))+(q.note&&!elsewhere?" · "+q.note:"")));
    li.append(text);
    if(q.state==="refused"){
      const edit=node("button","Edit");edit.ariaLabel="Edit waiting question: "+q.objective;
      edit.onclick=()=>changeWaiting(rows=>rows.filter(x=>x.id!==r.id)).then(()=>{
        $("objective").value=q.objective;$("researchmode").value=q.mode||"";buttons();$("objective").focus();
        say("The question is back in the field. Change it, then submit it again.");}).catch(e=>say(e.message));
      li.append(edit);
    }
    const remove=node("button","Remove");remove.ariaLabel="Remove waiting question: "+q.objective;
    remove.onclick=()=>{
      if(!confirm("Remove this waiting question from this device? If an earlier try already reached your Spark, it stays there."))return;
      changeWaiting(rows=>rows.filter(x=>x.id!==r.id)).then(()=>say("Waiting question removed from this device.")).catch(e=>say(e.message));
    };
    li.append(remove);list.append(li);
  }
  $("researchoutbox").hidden=!items.length;
  $("outboxhelp").textContent=(vaultOpen()?"Kept in your encrypted vault on this device for up to "+outbox.OUTBOX_DAYS+" days."
    :"Kept in this tab only. Open your vault (Device memory) to keep waiting questions through a reload.")
    +" Pocket sends them once your Spark answers, each one only once.";
  $("outboxsend").disabled=draining||!paired||!research||!outbox.due(items,pin).length;
  $("outboxsend").textContent=draining?"Sending…":"Send now";
  layout();
}
function scheduleOutbox(){
  clearTimeout(outboxTimer);outboxTimer=0;
  if(research&&paired&&!document.hidden&&outbox.due(allWaiting(),window.pocketSpark?.()).length)
    outboxTimer=setTimeout(drain,outbox.backoff(outboxTries)*1000);
}
// Send what waits for this Spark, oldest first, one at a time; stop at the first reply that says "not now".
async function drain(){
  clearTimeout(outboxTimer);outboxTimer=0;
  if(draining||!paired||!research||document.hidden)return;
  const items=outbox.due(allWaiting(),window.pocketSpark?.());
  if(!items.length)return;
  draining=true;drawOutbox();
  let sent=0,gone=0,later=null;
  try{
    for(const item of items){
      if(!paired||!research||document.hidden)break;
      let error=null;
      try{await call("research_submit",outbox.request(item));}catch(e){error=e;}
      const what=outbox.outcome(error);
      if(what==="sent"||what==="gone"){
        await changeWaiting(rows=>rows.filter(r=>r.id!==item.id)).catch(()=>{});  // if it stays, the next send is replayed
        if(what==="sent")sent++;else gone++;
      }else if(what==="refused"){
        await changeWaiting(rows=>outbox.replace(rows,item.id,{state:"refused",note:refusedNote(error)})).catch(()=>{});
      }else{
        later=error;
        await changeWaiting(rows=>outbox.replace(rows,item.id,{note:laterNote(error),tries:item.outbox.tries+1})).catch(()=>{});
        break;
      }
    }
  }finally{draining=false;}
  outboxTries=later?outboxTries+1:0;
  heldBy=!later?null:later.status===0&&later.code!=="unverified"?"network":"spark";
  if(later&&(later.status===404||later.status===403))fail(later);   // research switched off or the pairing refused
  const parts=[];
  if(sent)parts.push(sent===1?"Sent a waiting question to your Spark.":"Sent "+sent+" waiting questions to your Spark.");
  if(gone)parts.push(gone===1?"One waiting question had already reached your Spark and was removed there since; it was not sent again."
    :gone+" waiting questions had already reached your Spark and were removed there since; they were not sent again.");
  if(parts.length)say(parts.join(" "));
  drawOutbox();scheduleOutbox();
  if(sent)refresh();
}
// Questions kept in this tab only are lost with it: the browser asks before the page is left or reloaded.
window.addEventListener("beforeunload",e=>{if(tabOutbox.length){e.preventDefault();e.returnValue="";}});
function cites(numbers){
  const span=node("span",null,"cites");
  for(const n of numbers){const a=node("a","["+n+"]");a.href="#rsrc-"+n;a.ariaLabel="Source "+n;span.append(" ",a);}
  return span;
}
function list(title,items,view){
  if(!items?.length)return;
  view.append(node("h4",title));const ul=node("ul",null,"rnotes");
  for(const text of items)ul.append(node("li",text));
  view.append(ul);
}
// "3 copies, 1 independent source" when several cited documents repeat one origin (parity with the console).
function copies(x){
  const c=x?.copies,i=x?.independent_sources;
  return typeof c==="number"&&typeof i==="number"&&c>i?c+" copies, "+i+" independent source"+(i===1?"":"s"):null;
}
function rated(status,confidence){
  const text=[status==="summary"?null:STATUS[status],pct(confidence)].filter(Boolean).join(" · ");
  return text?node("small",text,"rconf"):null;
}
// One persistent live region (#researchstage) announces stage changes; only #researchbody is rebuilt (U-27).
function show(t,saved=false){
  if(reading&&reading.id!==t.id)stopReading();
  shown=saved?null:t;
  $("researchview").hidden=false;$("researchq").textContent=t.objective;
  const stageLine=saved?"Saved copy · "+(LABEL[t.status]||t.status):stageText(t);
  if($("researchstage").textContent!==stageLine)$("researchstage").textContent=stageLine;
  const view=$("researchbody");view.replaceChildren();
  const r=t.report;
  if(r){
    const v=r.verdict;
    if(v&&VERDICT[v.label]){
      view.append(node("h4","Verdict"));
      const p=node("p",null,"rverdict");p.append(node("strong",VERDICT[v.label]));
      if(pct(v.confidence))p.append(" · "+pct(v.confidence));
      view.append(p);
      if(v.statement){const claim=node("p","Claim checked: "+v.statement,"rclaim");claim.append(cites(v.citations||[]));view.append(claim);}
    }
    // R6 D8: the model-written answer and its rules note, as the console shows them; the quoted extractive answer
    // stays the evidence of record below it
    const x=r.reasoning, model=!!(x&&typeof x.generated_by==="string"&&x.generated_by.startsWith("model:")&&x.answer?.length);
    if(x&&!model)for(const text of [x.unanswered,x.note])if(text)view.append(node("p",text,"rnote"));
    if(model){
      view.append(node("h4","Answer"));
      const label=node("p",null,"rmodel");label.id="rmodellabel";
      label.append(node("span","Model-written summary","badge")," Written by "+x.generated_by.slice(6)+" from the collected evidence. "+
        "Each sentence was checked against its cited sources; single-source statements are marked.");
      view.append(label);
      const ol=node("ol",null,"rmodelanswer");ol.id="rmodelanswer";
      for(const s of x.answer){
        const li=node("li");li.append(node("span",s.text),cites(s.citations||[]));
        const marks=[...new Set((s.states||[]).map(k=>STATUS[k]||(k==="supported"?"Supported":null)).filter(Boolean))];
        if(s.flagged)marks.push("Self-check flagged (not confirmed by the checks)");
        if(marks.length)li.append(node("small",marks.join(" · "),"rconf"));
        ol.append(li);
      }
      view.append(ol);
      if(x.note)view.append(node("p",x.note,"rnote"));
    }
    view.append(node("h4",model?"Extractive summary (quoted evidence)":"Answer"));
    for(const item of r.answer||[]){
      const block=item.type==="quote"?node("blockquote"):node("p");
      block.append(node("span",item.type==="quote"?"“"+item.text+"”":item.text),cites(item.citations||[]));
      const rating=rated(item.status,item.confidence);if(rating)block.append(rating);
      const dup=copies(item)||copies((r.findings||[]).find(f=>f.statement===item.text));if(dup)block.append(node("small",dup,"rcopies"));
      view.append(block);
    }
    if(r.contradictions?.length){
      view.append(node("h4","Contradictions"));const ul=node("ul",null,"rnotes");
      for(const c of r.contradictions){
        const li=node("li");
        for(const side of [c.a,c.b])if(side){const p=node("p",side.text);p.prepend("["+side.citation+"] ");li.append(p);}
        li.append(node("small",c.reason+(c.assessment?" · "+c.assessment:"")));ul.append(li);
      }
      view.append(ul);
    }
    list("Uncertainties",r.uncertainties,view);
    list("Limitations",r.limitations,view);
    if(r.freshness?.class)view.append(node("p","Freshness: "+r.freshness.class+(r.freshness.satisfied?" · recent enough":" · may be out of date"),"rfresh"));
    view.append(node("h4","Sources"));const ol=node("ol",null,"rsources");
    for(const s of r.sources||[]){
      const li=node("li");li.id="rsrc-"+s.n;
      const href=safeUrl(s.url), title=s.title||s.domain||s.url;
      if(href){const a=node("a",title);a.href=href;a.rel="noopener noreferrer";a.target="_blank";li.append(a);}else li.append(node("span",title));
      li.append(node("small",[s.domain,day(s.published_at)?"Published "+day(s.published_at):"Undated",
        day(s.retrieved_at)?"Retrieved "+day(s.retrieved_at):null].filter(Boolean).join(" · ")));
      li.append(node("span",s.retrieval_mode==="cache"?"From cache":"Live","badge"));
      ol.append(li);
    }
    view.append(ol);
  }else if(!saved)view.append(node("p",active(t)?"The report appears here when your Spark finishes.":"No report was produced."));
  if(!saved)view.append(sessionBox(t));
  const actions=node("div",null,"buttons");
  if(r&&"speechSynthesis" in window&&spoken(r).length){
    const aloud=node("button",reading?.id===t.id?"Stop reading":"Read aloud");aloud.id="readaloud";
    aloud.onclick=()=>readAloud(t.id,r);actions.append(aloud);
    // offered only when an on-device voice exists; otherwise there is nothing to press
    if(!reading&&voiceKnown!==true){aloud.hidden=true;if(voiceKnown===undefined)localVoice().then(v=>{voiceKnown=!!v;if(v){const b=$("readaloud");if(b)b.hidden=false;}}).catch(()=>{voiceKnown=false;});}
  }
  if(saved){
    const forget=node("button","Forget saved copy");forget.onclick=()=>forgetFinding(t.id).then(()=>say("Saved finding forgotten on this device.")).catch(e=>say(e.message));
    actions.append(forget);
  }else{
    if(r){const save=node("button","Save for offline");save.onclick=()=>saveOffline(t);actions.append(save);}
    if(r){const review=node("button","Review evidence");review.onclick=()=>reviewEvidence(t.id);actions.append(review);}
    if(active(t)&&!t.cancel_requested){const stop=node("button","Cancel research");stop.onclick=()=>cancelTask(t.id);actions.append(stop);}
    if(["partial","cancelled","failed"].includes(t.status)){const more=node("button","Continue research");more.onclick=()=>continueTask(t.id);actions.append(more);}
  }
  const shut=node("button","Close");shut.onclick=()=>{close();$("research").focus();};actions.append(shut);
  view.append(actions);
}
function close(){stopReading();current=null;shown=null;session=null;$("researchbody").replaceChildren();$("researchq").textContent="";$("researchstage").textContent="";$("researchview").hidden=true;}
// Read aloud: only the quoted answer sentences, each followed by "Source n" for its citations. A voice the browser
// marks as remote (localService false) is never used, so the text stays on this device.
function spoken(r){
  return (r.answer||[]).filter(x=>x.type==="quote"&&x.text).map(x=>x.text.replace(/[\s.!?…]+$/,"")+"."+(x.citations?.length?" "+x.citations.map(n=>"Source "+n).join(", ")+".":""));
}
let voiceKnown;
async function localVoice(){
  let all=speechSynthesis.getVoices();
  if(!all.length)await new Promise(done=>{const t=setTimeout(done,1000);speechSynthesis.addEventListener("voiceschanged",()=>{clearTimeout(t);done();},{once:true});});
  all=speechSynthesis.getVoices();
  return all.find(v=>v.localService&&/^en/i.test(v.lang))||all.find(v=>v.localService)||null;
}
function stopReading(){
  if(!reading)return;
  reading=null;try{speechSynthesis.cancel();}catch{}
  const button=$("readaloud");if(button)button.textContent="Read aloud";
}
async function readAloud(id,r){
  if(reading){stopReading();say("Stopped reading.");return;}
  const mine={id};reading=mine;
  const voice=await localVoice();
  if(reading!==mine)return;
  if(!voice){reading=null;say("No on-device voice is available, so nothing was read aloud.");return;}
  const parts=spoken(r);
  parts.forEach((text,i)=>{
    const u=new SpeechSynthesisUtterance(text);u.voice=voice;u.lang=voice.lang;
    if(i===parts.length-1)u.onend=u.onerror=()=>{if(reading===mine)stopReading();};
    speechSynthesis.speak(u);
  });
  const button=$("readaloud");if(button)button.textContent="Stop reading";say("Reading the quoted answer aloud with an on-device voice.");
}
// Local notifications, opt-in for this visit. Permission is asked only from this switch. A notification is shown
// only when a poll of this open, visible page sees a question finish while the page lacks focus (another window
// or split screen); hidden pages do not poll and this path uses no push service (a closed Pocket is push's job).
function finished(t){
  if(pushOn||!notify||Notification.permission!=="granted"||document.hasFocus())return;  // push already tells this device
  navigator.serviceWorker?.getRegistration?.().then(reg=>reg?.showNotification("Pocket research finished",
    {body:(LABEL[t.status]||"Finished")+". Open Pocket to read it.",tag:"pocket-research-"+t.id,icon:"/icon.svg"})).catch(()=>{});
}
$("researchnotify").hidden=$("researchnotifyhelp").hidden=!("Notification" in window&&"serviceWorker" in navigator);
$("notifyswitch").onchange=async e=>{
  const box=e.target;
  if(!box.checked){notify=false;say("Research notifications off.");return;}
  let permission=Notification.permission;
  if(permission==="default"){try{permission=await Notification.requestPermission();}catch{permission="denied";}}
  notify=box.checked=permission==="granted";
  say(notify?"Notifications on for this visit while Pocket stays open.":"Notifications are blocked for this site in your browser settings.");
};
// Web Push (ADR-040), opt-in per device: the Spark sends an empty push through this browser's push service when a
// question of this family finishes (or an autonomy finding reaches the owner). Nothing about the question is sent; the
// service worker shows a generic notification and Pocket loads the details from the Spark when opened. Permission is
// asked only from this switch. Without PushManager (iOS outside an installed home-screen app) Pocket works as before.
const pushable=()=>"serviceWorker" in navigator&&"PushManager" in window&&"Notification" in window;
const apple=()=>/iP(hone|ad|od)/.test(navigator.userAgent)||(navigator.platform==="MacIntel"&&navigator.maxTouchPoints>1);
const installed=()=>matchMedia("(display-mode: standalone)").matches||navigator.standalone===true;
function keyBytes(b64){const s=atob(b64.replace(/-/g,"+").replace(/_/g,"/")+"===".slice((b64.length+3)%4));return Uint8Array.from(s,c=>c.charCodeAt(0));}
const sameKey=(a,b)=>!!a&&a.byteLength===b.length&&new Uint8Array(a).every((x,i)=>x===b[i]);
async function browserSub(){return pushable()?(await navigator.serviceWorker.ready).pushManager.getSubscription():null;}
function pushView(){
  const supported=pushable();
  $("pushbox").hidden=!push||!supported;
  $("pushhint").hidden=!push||supported;
  $("pushhint").textContent=apple()&&!installed()
    ?"Notifications while Pocket is closed: on iPhone and iPad, add Pocket to your Home Screen (Share, then Add to Home Screen) and open it from there (iOS 16.4 or later). Everything else works here as it is."
    :"This browser cannot receive push notifications. Everything else in Pocket works; the switch above notifies you while Pocket is open.";
  if($("pushbox").hidden)return;
  const p=Notification.permission;
  if(!pushBusy)$("pushswitch").checked=pushOn;  // while busy the switch shows what was asked for
  $("pushstate").textContent="Browser permission: "+(p==="granted"?"allowed":p==="denied"?"blocked (change it in this site's browser settings)":"not asked yet")+
    " · "+(pushOn?"this device is subscribed on your Spark":pushOld?"not subscribed (this browser still holds a push subscription from an earlier pairing; turn push on to move it here)":"not subscribed");
  $("pushtest").disabled=!pushOn;
}
async function loadPush(){
  push=null;pushOn=false;pushOld=false;
  if(!paired||!research||!offered){pushView();return;}  // `state` says whether this Spark offers push
  const g=generation;
  let status;
  try{status=await call("push",{action:"status"});}catch{if(g===generation)pushView();return;}  // 404: no push here
  if(g!==generation)return;
  push=status;
  const sub=await browserSub().catch(()=>null);
  if(g!==generation)return;
  if(sub&&status.subscribed){pushOn=true;call("push",{action:"subscribe",endpoint:sub.endpoint}).catch(()=>{});}  // a rotated endpoint
  else if(status.subscribed)call("push",{action:"unsubscribe"}).catch(()=>{});  // site data cleared: nothing to reach
  else pushOld=!!sub;
  pushView();
}
// Turning push off: the Spark first, then this browser, so neither keeps a subscription even if the Spark is away.
async function pushOff(){
  try{await call("push",{action:"unsubscribe"});}
  finally{pushOn=false;pushOld=false;const sub=await browserSub().catch(()=>null);await sub?.unsubscribe().catch(()=>{});}
}
// Called by mobile.js when the Spark erased the pairing (revoked) or the person forgot it, never when the vault merely
// locks on hide: the browser's subscription must outlive a closed Pocket, which is the point of push.
window.pocketPushDrop=()=>browserSub().then(sub=>sub?.unsubscribe()).catch(()=>{});
// R6 setup (g): opting out needs neither the vault nor a pairing. Reopened without the vault (POC-07: the pairing
// lived in tab memory), Pocket is "Phone only", yet this browser still holds its push subscription. The Your Spark
// card then offers to drop it here; with the subscription gone the push service answers the Spark 404/410 at its next
// push, and the Spark deletes its copy (ADR-040), so nothing more is sent or shown.
async function drawOptOut(){
  const box=$("pushoptout");
  if(paired||!pushable()){box.hidden=true;return;}
  const sub=await browserSub().catch(()=>null);
  box.hidden=paired||!sub;
}
$("pushoptoutbtn").onclick=async()=>{
  const sub=await browserSub().catch(()=>null);
  await sub?.unsubscribe().catch(()=>{});
  $("pushoptout").hidden=true;
  $("pushoptoutmsg").textContent="Push is off on this device. Nothing more is shown here; your Spark deletes its copy "+
    "of the subscription the next time it tries to send.";
};
// 429 push_limit: a device turns push on (or moves it to a new push address) and asks for a test push at most 5 times
// an hour each, so it cannot make the Spark ignore a push service's Retry-After.
const pushLimit=e=>{const m=Math.max(1,Math.ceil((Number(e.body?.retry_after)||3600)/60));
  return "Your Spark accepts at most 5 push changes and 5 test pushes an hour from this device. Try again in about "+m+" minute"+(m===1?"":"s")+".";};
$("pushswitch").onchange=async e=>{
  const box=e.target, g=generation;
  if(pushBusy||!push){box.checked=pushOn;return;}
  pushBusy=true;pushView();
  try{
    if(!box.checked){await pushOff();if(g===generation)say("Push off. Your Spark no longer sends to this device, and this browser dropped its push subscription.");return;}
    let permission=Notification.permission;
    if(permission==="default"){try{permission=await Notification.requestPermission();}catch{permission="denied";}}
    if(permission!=="granted"){if(g===generation)say("Notifications are blocked for this site in your browser settings.");return;}
    const manager=(await navigator.serviceWorker.ready).pushManager, key=keyBytes(push.key);
    let sub=await manager.getSubscription();
    if(sub&&!sameKey(sub.options?.applicationServerKey,key)){await sub.unsubscribe();sub=null;}  // another Spark's key
    sub=sub||await manager.subscribe({userVisibleOnly:true,applicationServerKey:key});
    if(g!==generation)return;
    await call("push",{action:"subscribe",endpoint:sub.endpoint});
    pushOn=true;pushOld=false;
    say("Push on for this device. Your Spark sends an empty push when a question finishes; the question never leaves your Spark.");
  }catch(err){
    if(g!==generation)return;
    if(err.code==="push_service"){(await browserSub().catch(()=>null))?.unsubscribe().catch(()=>{});say("Your Spark does not send to this browser's push service, so push stays off. Pocket works without it.");}
    else if(err.code==="push_limit")say(pushLimit(err));
    else if(err.status===404){push=null;say("Your Spark does not offer push right now.");}
    else if(typeof err.status==="number")fail(err);
    else say("This browser could not set up push ("+(err.name||"error")+"), so it stays off. Pocket works without it.");
  }finally{pushBusy=false;if(g===generation)pushView();}
};
$("pushtest").onclick=async()=>{
  if(pushBusy||!pushOn)return;
  const g=generation;pushBusy=true;pushView();
  try{
    const {after}=await call("push",{action:"test"});if(g!==generation)return;
    pushTesting=true;
    say(after>0?"Test push queued. Your Spark sends at most one push a minute to a device; this one leaves in about "+after+" s."
      :"Test push requested. A notification saying “Your Spark finished a research task” should appear within a few seconds.");
  }catch(e){
    if(g!==generation)return;
    if(e.code==="not_subscribed"){pushOn=false;say("Your Spark has no push subscription for this device. Turn push on again.");}
    else if(e.code==="push_limit")say(pushLimit(e));
    else if(e.status===404){push=null;say("Your Spark does not offer push right now.");}
    else fail(e);
  }finally{pushBusy=false;if(g===generation)pushView();}
};
navigator.serviceWorker?.addEventListener("message",e=>{
  if(e.data?.type!=="pocket-push")return;
  if(pushTesting){pushTesting=false;say("A push from your Spark arrived on this device.");}
  refresh();
});
navigator.serviceWorker?.startMessages?.();
async function saveOffline(t){
  if(!vaultOpen()){say("Open your device vault first (Device memory → Keep episodes on this device). Nothing was stored.");return;}
  const entry={id:t.id,created:Date.now(),keep:"day",research:{id:t.id,objective:t.objective,status:t.status,mode:t.mode,updated:t.updated,report:t.report}};
  if(JSON.stringify(entry).length>MAX_SAVED_BYTES){say("This report is too large to keep offline.");return;}
  try{await keepFinding(entry);say("Saved in your encrypted vault for 24 hours.");}catch(e){say(e.message||"Could not save the finding.");}
}
async function openTask(id){
  if(!paired||!research)return;
  const g=generation;
  try{const t=await call("research_get",{id});if(g!==generation)return;current={id,saved:false};if(session?.id!==id)session=null;show(t);$("researchview").focus();loadSession(id);}
  catch(e){if(g===generation)fail(e);}
}
// Evidence review (ADR-035): each subject's verification state and basis, claim text shown as untrusted web text.
// The state is a word with a glyph and a border pattern, so colour is never the only signal.
const STATE={supported:"Supported",disputed:"Disputed",insufficient:"Insufficient evidence",conflicting:"Conflicting sources",
  temporally_unresolved:"Time not established",not_evaluated:"Not evaluated"};
const GLYPH={supported:"✓",disputed:"✗",insufficient:"?",conflicting:"≠",temporally_unresolved:"~",not_evaluated:"–"};
const BASIS={corroboration:"independent sources agree",official_source:"the subject's own site",registry_record:"the package registry record",
  version_marker:"the version number (pre-release marker)",contradiction:"contradicting evidence",owner_hypothesis:"your own hypothesis"};
// No basis: say what the evidence actually is (R2-06). A quoted statement came from at least one listed source; it was
// not confirmed independently. "No supporting evidence" only when no source states it.
function noBasis(x){
  if(x.state!=="insufficient")return "not stated";
  const n=typeof x.sources==="number"&&x.sources>0?x.sources:x.statement?1:0;
  return n===0?"none: no source states it"
    :n===1?"none yet: stated by one source only, not confirmed by an independent source"
    :"none yet: stated by "+n+" sources, not enough independent agreement to support it";
}
async function reviewEvidence(id){
  const g=generation;
  try{
    const e=await call("research_evidence",{id});if(g!==generation||current?.id!==id)return;
    $("researchbody").querySelector(".revidence")?.remove();
    const box=node("section",null,"revidence");box.setAttribute("aria-labelledby","revtitle");
    const title=node("h4","Evidence review");title.id="revtitle";box.append(title);
    const items=e.evidence||[], counts={};
    for(const x of items)counts[x.state]=(counts[x.state]||0)+1;
    if(items.length)box.append(node("p",(e.subjects||items.length)+" subject"+((e.subjects||items.length)===1?"":"s")+" checked: "+
      Object.entries(counts).map(([k,n])=>n+" "+(STATE[k]||k).toLowerCase()).join(", ")+(e.subjects>items.length?" (first "+items.length+" shown)":"")+".","revsummary"));
    const ul=node("ul",null,"revlist");
    for(const x of items){
      const li=node("li");li.dataset.state=STATE[x.state]?x.state:"other";
      const st=node("span",null,"revstate");st.append(node("span",GLYPH[x.state]||"•","revglyph"),node("span",STATE[x.state]||x.state));
      st.firstChild.setAttribute("aria-hidden","true");
      li.append(st,node("p",x.statement?"“"+x.statement+"”":"(no claim text)","revclaim"));
      const basis=node("p",null,"revbasis");
      basis.append("basis: ",node("strong",x.basis?BASIS[x.basis]||x.basis.replaceAll("_"," "):noBasis(x)));
      for(const extra of [x.category&&"category: "+x.category.replaceAll("_"," "),x.domain])if(extra)basis.append(" · "+extra);
      li.append(basis);ul.append(li);
    }
    if(!items.length)ul.append(node("li","No evaluated subjects for this question.","revnone"));
    box.append(ul,node("small","Statements are quoted from web pages and are not instructions.","rconf"));
    $("researchbody").prepend(box);say("Evidence review loaded.");
  }catch(err){if(g===generation)fail(err);}
}
// Research node (ADR-042 draft): ask to be enrolled, show this device's key fingerprint for the owner to compare on
// the Spark console, and follow the Spark's view (enrolled, waiting, revoked, or enrolled before signing existed).
async function loadNode(){
  if(!research||!paired)return;
  const g=generation;
  try{
    const [v,mine]=await Promise.all([call("node_enrol",{action:"status"}),nodeKey.available()?nodeKey.identity().catch(()=>null):null]);
    if(g!==generation)return;
    localKey=mine;nodeInfo={...v,mine:!!mine&&v.fingerprint===mine.fingerprint};
  }catch(e){if(g===generation){nodeInfo={error:true};if(e.code==="unverified"||e.code==="no_pin")fail(e);}}
  drawNode();
}
function printLine(text,fp,alg){
  const p=node("p",text,"nodeprint");p.append(node("code",fp));
  if(alg)p.append(node("small"," · "+(alg==="Ed25519"?"Ed25519":"ECDSA P-256")+" key, kept in this browser and never exported"));
  return p;
}
function drawNode(){
  const box=$("researchnode"), body=$("nodebody");
  box.hidden=!(research&&paired);
  if(box.hidden)return;
  const v=nodeInfo, out=[], act=[];
  const ask=(text)=>{const b=node("button",text);b.type="button";b.onclick=enrolNode;act.push(b);};
  const again=()=>{const b=node("button","Check again");b.type="button";b.onclick=()=>loadNode().then(()=>say(nodeInfo?.state==="enrolled"?"This device is now a research node.":"Still waiting for the owner."));act.push(b);};
  if(!v)out.push(node("p","Checking with your Spark…"));
  else if(v.error)out.push(node("p","Pocket could not check this device's node status with your Spark."));
  else if(v.state==="enrolled"&&v.mine){
    out.push(node("p","This device is the research node “"+v.label+"”. Its requests to take, hand off, continue or cancel research are signed with its own key."));
    out.push(printLine("Fingerprint: ",v.fingerprint,v.alg));
  }else if(v.pending==="attested"&&localKey&&v.pending_fingerprint===localKey.fingerprint){
    out.push(node("p","Waiting for the owner. On the Spark console (Autonomy, Research nodes), the owner compares this fingerprint with the one listed there and enrols this device."));
    out.push(printLine("This device's fingerprint: ",localKey.fingerprint,localKey.alg));again();
  }else{
    if(v.pending==="attested")out.push(node("p","Your Spark holds an enrolment request whose key is not this browser's. Ask again so the owner sees this device's key.","rwarn"));
    out.push(node("p",v.state==="reenrol"?"Your Spark now requires research nodes to prove who they are. This device was enrolled before that, so it must enrol again before it can drive research."
      :v.state==="revoked"?"The owner revoked this device as a research node, and your Spark deleted its key. It can still ask and read research."
      :v.state==="enrolled"?"Your Spark lists this device as a research node, but this browser no longer holds its key (site data cleared?). Enrol it again."
      :"This device is not a research node. A research node can take a research session, hand it to another of your devices and accept one. It signs those requests with a key that never leaves this browser."));
    if(!nodeKey.available())out.push(node("p","This browser cannot keep a signing key, so it cannot become a research node."));
    else ask(v.state==="none"?"Ask to enrol this device":"Enrol this device again");
  }
  const pin=window.pocketSpark?.();
  if(pin)out.push(node("small","Your Spark's fingerprint, pinned at pairing: "+pin.fingerprint,"rconf"));
  if(act.length){const row=node("div",null,"buttons");row.append(...act);out.push(row);}
  for(const b of act)b.disabled=nodeBusy;
  body.replaceChildren(...out);
}
async function enrolNode(){
  if(nodeBusy)return;
  nodeBusy=true;drawNode();const g=generation;
  try{
    const id=await nodeKey.identity(true);
    const challenge=await call("node_enrol",{action:"challenge"});
    const signature=nodeKey.b64(await nodeKey.sign(nodeKey.canonicalChallenge(challenge)));
    const v=await call("node_enrol",{action:"attest",challenge:challenge.challenge,alg:id.alg,public_key:id.publicKey,signature});
    if(g!==generation)return;
    localKey=id;nodeInfo={...v,mine:v.fingerprint===id.fingerprint};
    say(v.pending_fingerprint===id.fingerprint?"Enrolment requested. Ask the owner to compare the fingerprint "+id.fingerprint+" on the Spark console and enrol this device."
      :"Your Spark recorded a different fingerprint than this device's key. Do not let the owner approve it; ask again.");
  }catch(e){if(g===generation){if(e.code==="no_challenge"||e.code==="challenge_expired")say("The enrolment request expired. Ask again.");else fail(e);}}
  finally{nodeBusy=false;if(g===generation){drawNode();$("nodebody").querySelector(".nodeprint code, button")?.focus?.();}}
}
// Session ownership (RESEARCH_POLICY.md section 6): one device of the family drives a research session at a time.
// Any linked device reads the status; enrolled research nodes take, hand off (to another node by its name), accept
// and release. While another device drives it, Cancel and Continue are refused here (409 owned).
const SESSION_DONE={take:"This device now drives this research. Your other devices can read it but not cancel or continue it.",
  accept:"Hand-off accepted: this device now drives this research.",
  release:"Released. Any of your linked devices can drive this research again."};
async function loadSession(id){
  const g=generation;
  try{const v=await call("research_session",{id,action:"status"});if(g===generation&&current?.id===id)setSession(id,v);}
  catch(e){if(g===generation&&current?.id===id){session={id,error:true};redrawSession();}}
}
function setSession(id,v){session={id,view:{owner:v.owner??null,you:!!v.you,offered_to:v.offered_to??null,
  offered_to_you:!!v.offered_to_you,expires_in:v.expires_in}};redrawSession();}
function redrawSession(focus=false){
  const old=$("researchbody").querySelector(".rsession");
  if(!old||!shown)return;
  const fresh=sessionBox(shown);old.replaceWith(fresh);
  if(focus)fresh.querySelector(".rsessionstate").focus();
}
const minutesLeft=s=>typeof s==="number"&&s>0?Math.max(1,Math.round(s/60)):null;
function sessionButton(text,action,id){const b=node("button",text);b.type="button";b.onclick=()=>sessionAction(id,action);return b;}
function sessionBox(t){
  const box=node("section",null,"rsession");box.setAttribute("aria-labelledby","rsessiontitle");
  const title=node("h4","Who drives this research");title.id="rsessiontitle";box.append(title);
  const v=session?.id===t.id?session.view:null, line=node("p",null,"rsessionstate");line.tabIndex=-1;
  const chip=(who,text)=>{const c=node("span",null,"rowner");c.dataset.who=who;c.append(node("span",{nobody:"○",you:"●",offered:"◐",other:"◑"}[who],"revglyph"),node("span",text));c.firstChild.setAttribute("aria-hidden","true");return c;};
  const actions=node("div",null,"buttons");
  if(session?.id===t.id&&session.error){line.append("Pocket could not check which device drives this research.");box.append(line);return box;}
  if(!v){line.append("Checking which of your devices drives this research…");box.append(line);return box;}
  const mins=minutesLeft(v.expires_in);
  if(!v.owner&&!v.you){
    line.append(chip("nobody","Nobody")," No device drives this research. Any of your linked devices can cancel or continue it; take ownership to keep the others from doing so.");
    actions.append(sessionButton("Take ownership","take",t.id));
  }else if(v.you){
    line.append(chip("you","This device")," This device drives this research"+(mins?" for about "+mins+" more minute"+(mins===1?"":"s")+"; each action renews that":"")+".");
    if(v.offered_to)line.append(" Offered to “"+v.offered_to+"”: waiting for it to accept (an offer lasts 5 minutes).");
    const form=node("form",null,"rhandoff");form.noValidate=true;
    const label=node("label","Hand off to (the name of another of your devices)");label.htmlFor="handoffto";
    const input=node("input");input.id="handoffto";input.maxLength=64;input.autocomplete="off";input.setAttribute("aria-describedby","handoffhelp");
    const help=node("small","Use the name your owner gave that device when enrolling it as a research node. It takes over when it accepts.");help.id="handoffhelp";
    const offer=node("button","Offer hand-off");
    form.onsubmit=e=>{e.preventDefault();const to=input.value.trim();
      if(!to){say("Enter the name of the device to hand this research to.");input.focus();return;}
      sessionAction(t.id,"handoff",to);};
    form.append(label,input,help,node("div",null,"buttons"));form.lastChild.append(offer,sessionButton("Release","release",t.id));
    box.append(line,form);return box;
  }else if(v.offered_to_you){
    line.append(chip("offered","Offered to this device")," “"+(v.owner||"Another device")+"” offers this research to this device.");
    actions.append(sessionButton("Accept hand-off","accept",t.id));
  }else{
    line.append(chip("other","“"+(v.owner||"Another device")+"”")," “"+(v.owner||"Another device")+"” drives this research. Only it can cancel or continue it until it hands it over or releases it"+(mins?" (or in about "+mins+" minute"+(mins===1?"":"s")+" if it stays idle)":"")+".");
  }
  box.append(line);if(actions.childElementCount)box.append(actions);
  return box;
}
async function sessionAction(id,action,to){
  if(busy)return;
  const g=generation;busy=true;
  for(const b of $("researchbody").querySelectorAll(".rsession button"))b.disabled=true;
  try{
    const v=await call("research_session",action==="handoff"?{id,action,to}:{id,action});
    if(g!==generation||current?.id!==id)return;
    setSession(id,v);redrawSession(true);
    say(action==="handoff"?"Offered to “"+to+"”. It moves when that device accepts, within 5 minutes.":SESSION_DONE[action]);
  }catch(e){
    if(g!==generation||current?.id!==id)return;
    if(e.body&&"you" in e.body)setSession(id,e.body);
    redrawSession(true);
    const owner=e.body?.owner;
    const text=e.code==="not_a_node"?"This device is not enrolled as a research node, so it cannot own research. Ask to enrol it under “This device as a research node”; the owner confirms it on the Spark console."
      :e.code==="reenrol"?NODE_TEXT.reenrol
      :e.code==="no_such_node"?"No other enrolled device of your family is called “"+to+"”. Use the name your owner gave it."
      :e.code==="owned"?(action==="take"?"“"+(owner||"Another device")+"” drives this research. Ask it to hand it over."
        :action==="accept"?"This research was not offered to this device, or the offer expired. Ask the device that drives it to offer it again."
        :"Only the device that drives this research can do that. Take ownership first.")
      :null;
    if(text)say(text);else fail(e);
  }finally{busy=false;buttons();if(session?.id===id)for(const b of $("researchbody").querySelectorAll(".rsession button"))b.disabled=false;}
}
// Continue a cancelled, partial or failed question; any device of the same family may (unless another device owns it).
async function continueTask(id){
  const g=generation;
  try{const {id:next}=await call("research_continue",{id});if(g!==generation)return;say("Continued on your Spark.");await refresh();if(g===generation)await openTask(next);}
  catch(e){if(g===generation)fail(e);}
}
async function cancelTask(id){
  const g=generation;
  try{const t=await call("research_cancel",{id});if(g!==generation)return;update(t);if(current?.id===id)show(t);say(t.status==="cancelled"?"Research cancelled.":"Cancel requested. Your Spark stops before its next step.");schedule();}
  catch(e){if(g===generation)fail(e);}
}
function update(t){
  const row=tasks.find(x=>x.id===t.id);
  if(row)for(const k of ["status","stage","updated","cancel_requested","spent"])row[k]=t[k];
  drawList();
  if(!active(t)&&/^Queued on your Spark/.test($("researchmsg").textContent))say("");  // no stale "Queued" after it finished
}
async function refresh(){
  if(!research||!paired)return;
  const g=generation;
  try{
    const reply=await call("research_list",{});if(g!==generation)return;tasks=reply.tasks;
    workerRunning=typeof reply.worker_running==="boolean"?reply.worker_running:null;drawList();schedule();scheduleList();
    if(!draining&&heldBy!=="spark")drain();   // the Spark answers: send what waits, unless it said "not now"
    $("researchworker").hidden=workerRunning!==false;
    $("researchworker").textContent=workerRunning===false?NO_WORKER+": questions wait as Queued until your owner starts one on the Spark.":"";
    if(!nodeInfo||nodeInfo.error||nodeInfo.pending==="attested")loadNode();
    const left=away;away=null;
    const done=left?tasks.filter(t=>left.has(t.id)&&!active(t)):[];
    if(done.length)say("Finished while you were away: "+done.map(t=>"“"+t.objective+"” ("+(LABEL[t.status]||t.status)+")").join("; ")+".");
  }
  catch(e){if(g===generation)fail(e);}
}
function scheduleList(){
  clearTimeout(listTimer);listTimer=0;
  if(research&&paired&&!document.hidden)listTimer=setTimeout(refresh,LIST_MS);
}
// Poll only while a task is still running and the page is visible; a hidden page stops polling.
function schedule(){
  clearTimeout(timer);timer=0;
  if(research&&paired&&!document.hidden&&tasks.some(active))timer=setTimeout(poll,3000);
}
async function poll(){
  const g=generation;
  for(const t of tasks.filter(active).slice(0,3)){
    try{
      const seen=t.status+t.stage+t.cancel_requested, d=await call("research_get",{id:t.id});if(g!==generation)return;
      update(d);  // redraw the open view only on a change, so keyboard focus inside it survives polling
      if(!active(d))finished(d);
      if(current?.id===d.id&&!current.saved&&seen!==d.status+d.stage+d.cancel_requested)show(d);
    }catch(e){if(g!==generation)return;if(e.status===404){tasks=tasks.filter(x=>x.id!==t.id);drawList();}else{fail(e);return;}}
  }
  schedule();
}
function stopDictation(){
  const rec=dictation;dictation=null;
  if(rec){clearTimeout(rec.deadline);rec.onresult=rec.onend=rec.onerror=null;try{rec.abort();}catch{}}
  buttons();
}
// Same gate as Speak an activity: secure context, on-device recognition, installed US English pack. No cloud.
async function speechGate(){
  const Recognizer=window.SpeechRecognition||window.webkitSpeechRecognition;
  if(!window.isSecureContext||!Recognizer?.available||!("processLocally" in Recognizer.prototype))return null;
  try{return await Recognizer.available({langs:["en-US"],processLocally:true})==="available"?Recognizer:null;}catch{return null;}
}
$("dictate").onclick=async()=>{
  if(dictation||document.hidden)return;
  const Recognizer=await speechGate();
  if(!Recognizer||document.hidden){$("dictate").hidden=!Recognizer;say("On-device recognition unavailable. Type your question.");return;}
  const rec=new Recognizer();rec.processLocally=true;
  if(rec.processLocally!==true){$("dictate").hidden=true;say("On-device recognition unavailable. Type your question.");return;}
  rec.lang="en-US";rec.continuous=false;rec.interimResults=false;rec.maxAlternatives=1;
  dictation=rec;rec.deadline=setTimeout(stopDictation,15000);buttons();
  rec.onresult=e=>{
    if(dictation!==rec||document.hidden)return;
    const result=e.results[0];
    if(result?.isFinal){
      const field=$("objective"), text=(field.value.trim()+" "+result[0].transcript.trim()).trim();
      field.value=text.slice(0,MAX);say("Review the dictated question, then submit it yourself.");field.focus();
    }
    stopDictation();
  };
  rec.onerror=rec.onend=()=>{if(dictation===rec)stopDictation();};
  say("Microphone active · dictating on this device");
  try{rec.start();}catch{stopDictation();say("Dictation unavailable. Type your question.");}
};
$("objective").oninput=()=>{const f=$("objective");f.removeAttribute("aria-invalid");f.setAttribute("aria-describedby","objectivecount");buttons();};$("researchmode").onchange=buttons;
$("researchform").onsubmit=async e=>{
  e.preventDefault();
  const data=body(), objective=data.objective;
  if(busy||!paired||!research||!objective||[...objective].length>MAX||bytes(data)>MAX_BYTES)return;
  const found=privateKinds(objective);
  if(found.length){
    const words=found.map(k=>PRIVATE_WORDS[k]), list=words.length>1?words.slice(0,-1).join(", ")+" and "+words.at(-1):words[0];
    const f=$("objective");f.setAttribute("aria-invalid","true");f.setAttribute("aria-describedby","objectivecount researchmsg");
    say("Nothing was sent. The question contains "+list+". Remove personal details and submit again.");f.focus();return;
  }
  busy=true;buttons();const g=generation, item=outbox.entry(data,window.pocketSpark?.());
  try{
    const {id}=await call("research_submit",outbox.request(item));if(g!==generation)return;
    $("objective").value="";sharing=false;layout();say("Queued on your Spark. Its research worker runs it; this page follows each stage.");
    await refresh();if(g!==generation)return;
    if(workerRunning===false)say("Queued on your Spark, but "+NO_WORKER.charAt(0).toLowerCase()+NO_WORKER.slice(1)+": it waits until your owner starts one.");
    await openTask(id);
  }catch(err){
    // Out of reach, or a reply Pocket cannot confirm: the question may or may not have arrived. It waits here with
    // its key; sending it again later queues it at most once (outbox.mjs).
    if((err.status===0&&!["not_paired","no_pin"].includes(err.code))||err.code==="unverified"){
      try{
        await keepWaiting(item);
        if($("objective").value.trim()===objective){$("objective").value="";sharing=false;}
        say(err.code==="unverified"?"Pocket could not confirm your Spark's reply, so the question waits on this device and is asked again. It is never queued twice."
          :"Your Spark is out of reach, so the question waits on this device. Pocket sends it once your Spark answers.");
        outboxTries=0;heldBy="network";scheduleOutbox();
      }catch(e){say((e.message||"Could not keep the question.")+" Nothing was stored; the question is still in the field.");}
    }else if(g===generation)fail(err);
  }
  finally{busy=false;buttons();}
};
$("outboxsend").onclick=()=>{outboxTries=0;heldBy=null;drain();};
document.addEventListener("pocketpair",e=>{
  const next=!!e.detail?.paired, allowed=next&&e.detail?.research===true, pushes=allowed&&e.detail?.push===true;
  if(next===paired&&allowed===research&&pushes===offered){refresh();return;}
  if(paired&&!next)say("");  // a revoked or erased pairing: no stale outcome of the old one (RT-B-08)
  generation++;clearTimeout(timer);clearTimeout(listTimer);paired=next;research=allowed;offered=pushes;tasks=[];away=null;pushTesting=false;nodeInfo=null;localKey=null;
  if(current&&!current.saved)close();
  drawList();layout();drawNode();refresh();loadPush();drawOptOut();drawOutbox();
  if(sharing&&research&&paired&&$("objective").value)$("objective").focus();
});
drawOptOut();
document.addEventListener("pocketvault",()=>{drawSaved();drawOutbox();moveIntoVault();});
document.addEventListener("visibilitychange",()=>{
  if(document.hidden){
    away=new Set(tasks.filter(active).map(t=>t.id));
    generation++;clearTimeout(timer);clearTimeout(listTimer);clearTimeout(outboxTimer);outboxTimer=0;stopDictation();close();$("objective").value="";sharing=false;layout();
  }
  else refresh();
});
$("researchrefresh").onclick=()=>refresh();
// Web Share Target (manifest.webmanifest): Android opens /?share_url=…&share_text=…. The link is taken from the
// address bar at once, so it stays out of history, and only prefills "Summarize: <link>" for the owner to submit.
function sharedLink(){
  const q=new URLSearchParams(location.search);
  if(!["share_url","share_text","share_title"].some(k=>q.has(k)))return undefined;
  history.replaceState(null,"",location.pathname+location.hash);
  for(const raw of [q.get("share_url"),...((q.get("share_text")||"").match(/https?:\/\/\S+/gi)||[])]){
    const href=raw&&safeUrl(raw.trim());
    if(href&&!new URL(href).username&&!new URL(href).password)return href;
  }
  return null;
}
const shared=sharedLink();
if(shared!==undefined){
  sharing=true;
  if(!shared)say("Pocket can summarize shared web links (http or https) only. Nothing was sent.");
  else if([...(SUMMARIZE+shared)].length>MAX||bytes({objective:SUMMARIZE+shared})>MAX_BYTES)say("That link is too long for a 300-character question. Nothing was sent.");
  else{$("objective").value=SUMMARIZE+shared;say("Shared link ready. Review the question, then submit it yourself.");}
}
speechGate().then(R=>{$("dictate").hidden=!R;});
// Back online: the first send is the check that the Spark answers (one request; out of reach, it waits again).
window.addEventListener("online",()=>{buttons();if(heldBy!=="spark")drain();});window.addEventListener("offline",buttons);
drawSaved();drawOutbox();
