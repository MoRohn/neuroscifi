// Research from Pocket (ADR-028): queue a question for the Spark, follow its stages, read and save the report.
// Pocket never researches itself. Server text is rendered with textContent only; links are http(s) only.
// Shared links prefill the question for review (never submitted for you); Read aloud uses an on-device voice only;
// notifications are opt-in and come only from this open page (no push service), see mobile/README.md.
import {vaultOpen,findings,keepFinding,forgetFinding} from "./field-ui.mjs";
const $=id=>document.getElementById(id);
const TERMINAL=["completed","partial","failed","cancelled"], STAGES=["discovering","reading","evaluating","synthesizing"];
// Labels shared with the console (U-21): stages and statuses as the console's TASK_LABELS, kinds as #researchmode.
const LABEL={queued:"Queued",discovering:"Discovering",reading:"Reading",evaluating:"Evaluating",synthesizing:"Synthesizing",
  completed:"Completed",partial:"Partial",failed:"Failed",cancelled:"Cancelled"};
const STATUS={corroborated:"Corroborated",single_source:"Single source",contested:"Contested",insufficient:"Insufficient evidence"};
const VERDICT={supported:"Supported",disputed:"Disputed",contested:"Contested",insufficient:"Not enough evidence"};
const MAX=300, MAX_BYTES=1024, MAX_SAVED_BYTES=64000, SUMMARIZE="Summarize: ";
let research=false, paired=false, tasks=[], current=null, timer=0, generation=0, busy=false, dictation=null, savedSignature="";
let sharing=false, notify=false, away=null, reading=null, session=null, shown=null;
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
async function call(route,data){
  const token=window.pocketToken?.();
  if(!token)throw Object.assign(Error("Not paired"),{status:0,code:"not_paired"});  // local state, not a Spark refusal
  let response;
  try{
    response=await fetch("/api/"+route,{method:"POST",headers:{"Content-Type":"application/json",Authorization:"Bearer "+token},
      body:JSON.stringify(data),cache:"no-store",credentials:"omit"});
  }catch{if(window.pocketToken?.()===token)window.pocketReach?.(false);throw Object.assign(Error("Spark unreachable"),{status:0});}
  if(window.pocketToken?.()===token){if(response.status===403)window.pocketRefused?.();else window.pocketReach?.(true);}
  const body=await response.json().catch(()=>({}));
  if(!response.ok)throw Object.assign(Error("Spark returned "+response.status),{status:response.status,code:body.error,body});
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
  say(e.code==="daily_budget"?"Your Spark's daily research budget is used up. Nothing was queued. It renews tomorrow, or the owner can raise it."
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
function node(tag,text,cls){const e=document.createElement(tag);if(text!=null)e.textContent=text;if(cls)e.className=cls;return e;}
function safeUrl(u){try{const x=new URL(u);return x.protocol==="http:"||x.protocol==="https:"?x.href:null;}catch{return null;}}
const pct=c=>typeof c==="number"&&isFinite(c)&&c>=0&&c<=1?Math.round(c*100)+"% confidence":null;
const day=ts=>typeof ts==="number"&&isFinite(ts)?new Date(ts*1000).toLocaleDateString([], {year:"numeric",month:"short",day:"numeric"}):null;
function stageText(t){
  if(t.cancel_requested&&active(t))return "Cancelling…";
  if(t.status==="queued")return "Queued · waiting for your Spark's research worker";
  const i=STAGES.indexOf(t.status), s=t.spent||{};
  // incremental progress: what the Spark has spent on it so far (0.7.0 research_get.spent)
  const spent=typeof s.searches==="number"&&(s.searches||s.pages)?" · "+s.searches+" searches, "+(s.pages||0)+" pages so far":"";
  return i>=0?LABEL[t.status]+" · step "+(i+1)+" of 4"+spent:(LABEL[t.status]||String(t.status));
}
function layout(){
  const saved=vaultOpen()&&findings().length>0;
  $("research").hidden=!(research||saved||sharing); $("navresearch").hidden=$("research").hidden;
  $("researchform").hidden=!(research&&paired); $("researchtasks").hidden=!research;
  $("researchsavedbox").hidden=!saved;
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
  $("researchsubmithint").textContent=!$("researchsubmit").disabled?"":!paired||!research?"Pair with a research-enabled Spark to submit.":
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
    view.append(node("h4","Answer"));
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
// or split screen); hidden pages do not poll, and there is no push service, so a closed Pocket never notifies.
function finished(t){
  if(!notify||Notification.permission!=="granted"||document.hasFocus())return;
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
    const text=e.code==="not_a_node"?"This device is not enrolled as a research node, so it cannot own research. The owner can enrol it on the Spark (nsf web node enrol)."
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
    const reply=await call("research_list",{});if(g!==generation)return;tasks=reply.tasks;drawList();schedule();
    const left=away;away=null;
    const done=left?tasks.filter(t=>left.has(t.id)&&!active(t)):[];
    if(done.length)say("Finished while you were away: "+done.map(t=>"“"+t.objective+"” ("+(LABEL[t.status]||t.status)+")").join("; ")+".");
  }
  catch(e){if(g===generation)fail(e);}
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
  busy=true;buttons();const g=generation;
  try{
    const {id}=await call("research_submit",data);if(g!==generation)return;
    $("objective").value="";sharing=false;layout();say("Queued on your Spark. Its research worker runs it; this page follows each stage.");
    await refresh();if(g!==generation)return;await openTask(id);
  }catch(err){if(g===generation)fail(err);}
  finally{busy=false;buttons();}
};
document.addEventListener("pocketpair",e=>{
  const next=!!e.detail?.paired, allowed=next&&e.detail?.research===true;
  if(next===paired&&allowed===research){refresh();return;}
  if(paired&&!next)say("");  // a revoked or erased pairing: no stale outcome of the old one (RT-B-08)
  generation++;clearTimeout(timer);paired=next;research=allowed;tasks=[];away=null;
  if(current&&!current.saved)close();
  drawList();layout();refresh();
  if(sharing&&research&&paired&&$("objective").value)$("objective").focus();
});
document.addEventListener("pocketvault",drawSaved);
document.addEventListener("visibilitychange",()=>{
  if(document.hidden){
    away=new Set(tasks.filter(active).map(t=>t.id));
    generation++;clearTimeout(timer);stopDictation();close();$("objective").value="";sharing=false;layout();
  }
  else refresh();
});
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
drawSaved();
