// Research from Pocket (ADR-028): queue a question for the Spark, follow its stages, read and save the report.
// Pocket never researches itself. Server text is rendered with textContent only; links are http(s) only.
// Shared links prefill the question for review (never submitted for you); Read aloud uses an on-device voice only;
// notifications are opt-in and come only from this open page (no push service), see mobile/README.md.
import {vaultOpen,findings,keepFinding,forgetFinding} from "./field-ui.mjs";
const $=id=>document.getElementById(id);
const TERMINAL=["completed","partial","failed","cancelled"], STAGES=["discovering","reading","evaluating","synthesizing"];
const LABEL={queued:"Queued",discovering:"Discovering",reading:"Reading",evaluating:"Evaluating",synthesizing:"Synthesizing",
  completed:"Completed",partial:"Partial report",failed:"Failed",cancelled:"Cancelled"};
const STATUS={corroborated:"Corroborated",single_source:"Single source",contested:"Contested",insufficient:"Insufficient evidence"};
const VERDICT={supported:"Supported",disputed:"Disputed",contested:"Contested",insufficient:"Not enough evidence"};
const MAX=300, MAX_SAVED_BYTES=64000, SUMMARIZE="Summarize: ";
let research=false, paired=false, tasks=[], current=null, timer=0, generation=0, busy=false, dictation=null, savedSignature="";
let sharing=false, notify=false, away=null, reading=null;
const say=t=>{$("researchmsg").textContent=t;};
const active=t=>!TERMINAL.includes(t.status);

async function call(route,data){
  const token=window.pocketToken?.();
  if(!token)throw Object.assign(Error("Not paired"),{status:403});
  const response=await fetch("/api/"+route,{method:"POST",headers:{"Content-Type":"application/json",Authorization:"Bearer "+token},
    body:JSON.stringify(data),cache:"no-store",credentials:"omit"});
  const body=await response.json().catch(()=>({}));
  if(!response.ok)throw Object.assign(Error("Spark returned "+response.status),{status:response.status,code:body.error});
  return body;
}
function fail(e){
  if(e.status===404){research=false;tasks=[];if(current&&!current.saved)close();layout();say("Research is not available from this Spark.");return;}
  say(e.status===429?"Limit reached: up to 10 questions per hour from this device. Try again later."
    :e.code==="private_data"?"Remove personal details such as e-mail addresses, phone numbers or names you marked private. Nothing was sent."
    :e.status===400?"Your Spark rejected that request. Keep the question under 300 characters."
    :e.status===403?"Your Spark no longer accepts this pairing. Pair again."
    :"Spark unreachable. Saved findings stay readable on this device.");
}
function node(tag,text,cls){const e=document.createElement(tag);if(text!=null)e.textContent=text;if(cls)e.className=cls;return e;}
function safeUrl(u){try{const x=new URL(u);return x.protocol==="http:"||x.protocol==="https:"?x.href:null;}catch{return null;}}
const pct=c=>typeof c==="number"&&isFinite(c)&&c>=0&&c<=1?Math.round(c*100)+"% confidence":null;
const day=ts=>typeof ts==="number"&&isFinite(ts)?new Date(ts*1000).toLocaleDateString([], {year:"numeric",month:"short",day:"numeric"}):null;
function stageText(t){
  if(t.cancel_requested&&active(t))return "Cancelling…";
  if(t.status==="queued")return "Queued · waiting for your Spark's research worker";
  const i=STAGES.indexOf(t.status);
  return i>=0?LABEL[t.status]+" · step "+(i+1)+" of 4":(LABEL[t.status]||String(t.status));
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
function buttons(){
  const text=$("objective").value;
  $("objectivecount").textContent=text.length+" / "+MAX+" characters";
  $("researchsubmit").disabled=!paired||!research||busy||!text.trim()||text.length>MAX;
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
function rated(status,confidence){
  const text=[status==="summary"?null:STATUS[status],pct(confidence)].filter(Boolean).join(" · ");
  return text?node("small",text,"rconf"):null;
}
function show(t,saved=false){
  if(reading&&reading.id!==t.id)stopReading();
  const view=$("researchview");view.replaceChildren();view.hidden=false;
  view.append(node("h3",t.objective));
  const stage=node("p",saved?"Saved copy · "+(LABEL[t.status]||t.status):stageText(t),"rstage");stage.setAttribute("role","status");view.append(stage);
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
  const actions=node("div",null,"buttons");
  if(r&&"speechSynthesis" in window&&spoken(r).length){
    const aloud=node("button",reading?.id===t.id?"Stop reading":"Read aloud");aloud.id="readaloud";
    aloud.onclick=()=>readAloud(t.id,r);actions.append(aloud);
  }
  if(saved){
    const forget=node("button","Forget saved copy");forget.onclick=()=>forgetFinding(t.id).then(()=>say("Saved finding forgotten on this device.")).catch(e=>say(e.message));
    actions.append(forget);
  }else{
    if(r){const save=node("button","Save for offline");save.onclick=()=>saveOffline(t);actions.append(save);}
    if(active(t)&&!t.cancel_requested){const stop=node("button","Cancel research");stop.onclick=()=>cancelTask(t.id);actions.append(stop);}
  }
  const shut=node("button","Close");shut.onclick=()=>{close();$("research").focus();};actions.append(shut);
  view.append(actions);
}
function close(){stopReading();current=null;$("researchview").replaceChildren();$("researchview").hidden=true;}
// Read aloud: only the quoted answer sentences, each followed by "Source n" for its citations. A voice the browser
// marks as remote (localService false) is never used, so the text stays on this device.
function spoken(r){
  return (r.answer||[]).filter(x=>x.type==="quote"&&x.text).map(x=>x.text.replace(/[\s.!?…]+$/,"")+"."+(x.citations?.length?" "+x.citations.map(n=>"Source "+n).join(", ")+".":""));
}
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
  const g=generation;
  try{const t=await call("research_get",{id});if(g!==generation)return;current={id,saved:false};show(t);$("researchview").focus();}
  catch(e){if(g===generation)fail(e);}
}
async function cancelTask(id){
  const g=generation;
  try{const t=await call("research_cancel",{id});if(g!==generation)return;update(t);if(current?.id===id)show(t);say(t.status==="cancelled"?"Research cancelled.":"Cancel requested. Your Spark stops before its next step.");schedule();}
  catch(e){if(g===generation)fail(e);}
}
function update(t){
  const row=tasks.find(x=>x.id===t.id);
  if(row)for(const k of ["status","stage","updated","cancel_requested"])row[k]=t[k];
  drawList();
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
$("objective").oninput=buttons;
$("researchform").onsubmit=async e=>{
  e.preventDefault();
  const objective=$("objective").value.trim();
  if(busy||!paired||!research||!objective||objective.length>MAX)return;
  const data={objective};if($("researchmode").value)data.mode=$("researchmode").value;
  busy=true;buttons();const g=generation;
  try{
    const {id}=await call("research_submit",data);if(g!==generation)return;
    $("objective").value="";sharing=false;layout();say("Queued on your Spark. Its research worker runs it; this page follows each stage.");
    await refresh();await openTask(id);
  }catch(err){if(g===generation)fail(err);}
  finally{busy=false;buttons();}
};
document.addEventListener("pocketpair",e=>{
  const next=!!e.detail?.paired, allowed=next&&e.detail?.research===true;
  if(next===paired&&allowed===research){refresh();return;}
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
  else if((SUMMARIZE+shared).length>MAX)say("That link is too long for a 300-character question. Nothing was sent.");
  else{$("objective").value=SUMMARIZE+shared;say("Shared link ready. Review the question, then submit it yourself.");}
}
speechGate().then(R=>{$("dictate").hidden=!R;});
drawSaved();
