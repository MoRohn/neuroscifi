"use strict";
const el = id => document.getElementById(id);
let key = "", draft = null, selected = null, generation = 0, sharing = false, lastActive = 0;
const shared = new Map();  // ids this tab shared since pairing: memory only, never sent anywhere
const paused = channel => "Learning is paused for " + channel + ". Resume it under Evening review, or share without learning.";
const statusText = text => { el("message").textContent = text; };

async function api(route, data) {
  const headers = {"Content-Type": "application/json"};
  if (key) headers.Authorization = "Bearer " + key;
  const response = await fetch("/api/" + route, {method:"POST", headers, body:JSON.stringify(data), cache:"no-store", credentials:"omit"});
  if (!response.ok) throw Object.assign(Error("Spark returned " + response.status), {status: response.status});
  if ((route === "save" || route === "episode") && data.id) shared.set(data.id, route === "save" ? "forget" : "forget_episode");
  return response.json();
}
function review(item) {
  draft = item;
  el("draft").textContent = item ? item.channel + " · " + item.value : "No observation waiting.";
  el("learn").checked = false;
  el("waiting").textContent = item ? item.channel + " · " + item.value : "nothing";
  buttons();
}
function buttons() {
  el("save").disabled = !key || !draft || sharing;
  el("drop").disabled = !draft || sharing;
  el("auto").disabled = !key || !!selected;
  for (const id of ["sound","survey","light","listen","speak"]) el(id).disabled = !!selected;
  el("stop").disabled = !selected;
  el("evenbridge").hidden = !key;
  el("evenpair").hidden = !!key;
}
function sensorTime() {
  el("lastactive").textContent = el("sensor").dataset.live === "yes" ? "active now"
    : lastActive ? new Date(lastActive).toLocaleTimeString([], {hour:"numeric", minute:"2-digit"}) : "none since this page opened";
}
function item(channel, value) { return {id:crypto.randomUUID(), channel, value}; }
function stop(reason = "Sensors off") {
  generation++;
  const old = selected;
  selected = null;
  if (old) {
    clearInterval(old.timer);
    clearTimeout(old.deadline);
    if (old.rec) { old.rec.onresult = old.rec.onend = old.rec.onerror = null; try { old.rec.abort(); } catch {} }
    if (old.stream) old.stream.getTracks().forEach(track => track.stop());
    if (old.ctx) old.ctx.close().catch(()=>{});
    if (old.video) { old.video.pause(); old.video.srcObject = null; }
  }
  el("auto").checked = false;
  if (el("sensor").dataset.live === "yes") lastActive = Date.now();
  el("sensor").dataset.live = "no";
  sensorTime();
  el("status").textContent = reason;
  buttons();
}
async function acquire(kind, timed = false, survey = false) {
  if (selected || document.hidden) return;
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    statusText("Open this page over trusted HTTPS, or use localhost on the same device."); return;
  }
  const serial = ++generation;
  const run = {auto:timed && !!key && el("auto").checked, pending:false};
  selected = run; buttons();
  el("status").textContent = "Requesting " + (kind === "sound" ? "microphone" : "camera");
  // Also bounds a pending request or a camera that never produces a frame.
  run.deadline = setTimeout(()=>{ if (serial === generation) stop("Session ended"); },timed ? 300000 : 20000);
  try {
    const stream = await navigator.mediaDevices.getUserMedia(kind === "sound"
      ? {audio:{channelCount:1,echoCancellation:false,noiseSuppression:false,autoGainControl:false},video:false}
      : {audio:false,video:{facingMode:{ideal:"environment"},width:{ideal:320},height:{ideal:240}}});
    if (serial !== generation || document.hidden) {stream.getTracks().forEach(t=>t.stop()); return;}
    run.stream = stream;
    stream.getTracks().forEach(track=>{
      track.onended = track.onmute = ()=>{if (serial === generation) stop("Input interrupted");};
    });
    el("sensor").dataset.live = "yes"; sensorTime();
    if (kind === "light") {
      el("status").textContent = "Camera active · measuring light";
      const v = document.createElement("video");
      run.video = v; v.muted = true; v.playsInline = true; v.srcObject = stream;
      await v.play();
      if (serial !== generation) return;
      const canvas = document.createElement("canvas"); canvas.width = canvas.height = 1;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(v,0,0,1,1);
      const [r,g,b] = ctx.getImageData(0,0,1,1).data;
      const y = .2126*r+.7152*g+.0722*b;
      review(item("light", y<65 ? "dim" : y<175 ? "midrange" : "bright"));
      canvas.width = 0;
      stop(); return;
    }
    const ctx = new AudioContext(); run.ctx = ctx;
    await ctx.resume(); if (serial !== generation) return;
    const analyser = ctx.createAnalyser(); analyser.fftSize = 2048;
    ctx.createMediaStreamSource(stream).connect(analyser);
    const buf = new Float32Array(analyser.fftSize);
    const envelope=[];
    let power=0, count=0, lastShare=performance.now(), lastPreview=lastShare;
    el("status").textContent = survey ? "Microphone active · 8-second sound pattern" : timed ? "Listening · up to five minutes" : "Microphone active · sampling";
    run.timer = setInterval(async ()=>{
      if (serial !== generation) return;
      if (document.hidden) { stop(); return; }
      analyser.getFloatTimeDomainData(buf);
      let samplePower=0;
      for(const v of buf) samplePower+=v*v;
      power+=samplePower;
      if(survey) envelope.push(10*Math.log10(Math.max(samplePower/buf.length,1e-12)));
      count += buf.length; buf.fill(0);
      const now = performance.now();
      if(survey){
        if(now-lastPreview<8000)return;
        if(envelope.length<40){stop();statusText("Too few sound samples. Try again.");return;}
        envelope.sort((a,b)=>a-b);
        const spread=envelope[Math.floor(.90*envelope.length)]-envelope[Math.floor(.10*envelope.length)];
        review(item("dynamics",spread<6?"steady":spread<16?"variable":"wide"));stop();return;
      }
      if (now-lastPreview < 1500) return;
      const db = 10*Math.log10(Math.max(power/count,1e-12));
      const reading = item("sound", db < -45 ? "soft" : db < -22 ? "moderate" : "strong");
      power=0; count=0; lastPreview=now;
      if (!timed || !run.auto) review(reading);
      if (!timed) { stop(); return; }
      if (run.auto && !run.pending && now-lastShare >= 60000) {
        lastShare=now; run.pending=true;
        try { await api("save",{...reading,learn:true}); if (generation===serial) await historyView(); }
        catch (e) { if (generation===serial) { stop(); statusText(e.status===423 ? paused("sound")+" Automatic sharing stopped." : "Sync interrupted. Check history before restarting."); } }
        finally {run.pending=false;}
      }
    },100);
  } catch {
    if (serial === generation) {stop("Sensors off"); statusText("Capture unavailable. Check permissions or try a different input.");}
  }
}
async function spokenActivity() {
  if (selected || document.hidden) return;
  review(null);
  const Recognizer = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!window.isSecureContext || !Recognizer?.available || !("processLocally" in Recognizer.prototype)) {
    statusText("On-device recognition unavailable. Tap an activity instead."); return;
  }
  const serial = ++generation, run = {};
  selected = run; buttons();
  el("status").textContent = "Checking local speech support";
  run.deadline = setTimeout(()=>{if (serial===generation) stop("Voice input ended");},10000);
  try {
    const available = await Recognizer.available({langs:["en-US"],processLocally:true});
    if (serial!==generation || document.hidden) return;
    if (available!=="available") {
      stop(); statusText("Local US English pack unavailable. Tap an activity instead."); return;
    }
    const rec = new Recognizer(); run.rec=rec;
    rec.processLocally=true;
    if (rec.processLocally!==true) { stop(); statusText("Local recognition unavailable."); return; }
    rec.lang="en-US"; rec.continuous=false; rec.interimResults=false; rec.maxAlternatives=1;
    rec.onresult=e=>{
      if (serial!==generation || document.hidden) return;
      // Require the complete final utterance to be a single allowed label.
      const results=e.results;
      if (results.length===1 && results[0].isFinal) {
        const label=results[0][0].transcript.trim().toLowerCase().replace(/[.!?]$/,"");
        if (["focus","walk","rest","other"].includes(label)) {
          review(item("activity",label)); statusText("Review the suggested activity before sharing.");
        } else statusText("Speech discarded. Tap an activity or try a single label.");
      }
      stop();
    };
    rec.onerror=rec.onend=()=>{if (serial===generation) stop();};
    el("sensor").dataset.live="yes"; sensorTime();
    el("status").textContent="Microphone active · listening for one activity";
    rec.start();
  } catch {if(serial===generation){stop();statusText("Local speech unavailable. Tap an activity instead.");}}
}
async function forgetItem(r) {
  try {await api(r.episode?"forget_episode":"forget",{id:r.id}); await historyView(); el("refresh").focus();} catch {statusText("Could not confirm deletion. Refresh and retry.");}
}
async function setMute(channel, on) {
  try {
    const reply = await api(on ? "mute" : "unmute", {channel});
    await historyView(); el("refresh").focus();
    statusText(on ? "Learning paused for "+channel+". "+reply.unlearned+" observation(s) no longer change the forecast." : "Learning resumed for "+channel+". New observations can opt in again.");
  } catch {statusText("Could not confirm the change. Refresh and retry.");}
}
function reviewView(records, muted) {
  const learning = records.filter(r => r.learn), counts = {};
  el("changed").replaceChildren(); el("patterns").replaceChildren();
  for (const r of learning) {
    counts[r.channel] = (counts[r.channel] || 0) + 1;
    const li = document.createElement("li"), btn = document.createElement("button");
    li.append(document.createTextNode(r.channel+" · "+r.value));
    btn.textContent = "Forget this"; btn.ariaLabel = "Forget this: "+r.channel+" · "+r.value; btn.onclick = () => forgetItem(r);
    li.append(btn); el("changed").append(li);
  }
  if (!learning.length) { const li = document.createElement("li"); li.append(document.createTextNode("Nothing shared is changing the forecast.")); el("changed").append(li); }
  for (const channel of [...new Set([...Object.keys(counts), ...muted])].sort()) {
    const on = !muted.includes(channel), li = document.createElement("li"), btn = document.createElement("button");
    li.append(document.createTextNode(channel+(on ? " · "+counts[channel]+" learning" : " · learning paused")));
    btn.textContent = on ? "Stop learning this pattern" : "Resume learning"; btn.ariaLabel = btn.textContent+": "+channel;
    btn.onclick = () => setMute(channel, on); li.append(btn); el("patterns").append(li);
  }
}
async function forgetSession() {
  const ids = [...shared], owner = key;
  if (!ids.length) { el("sessionprogress").textContent = "Nothing shared from this tab since pairing."; return; }
  el("forgetsession").disabled = true;
  let done = 0;
  for (const [id, route] of ids) {
    if (key !== owner) break;
    el("sessionprogress").textContent = "Forgetting " + (done + 1) + " of " + ids.length + "…";
    try { await api(route, {id}); shared.delete(id); done++; } catch {}
  }
  el("sessionprogress").textContent = done === ids.length ? "Forgot all " + done + " items shared from this tab."
    : "Forgot " + done + " of " + ids.length + ". " + (ids.length - done) + " could not be confirmed. Retry to finish.";
  el("forgetsession").disabled = false;
  try { await historyView(); } catch {}
  el("refresh").focus();
}
async function historyView() {
  const requestedKey=key;
  const {records,prediction,muted} = await api("state",{});
  const {episodes} = await api("episodes",{});
  if (requestedKey!==key || document.hidden) return;
  el("history").replaceChildren();
  for (const r of [...records,...(episodes||[]).map(e=>({id:e.id,channel:"field episode",value:e.outcome,learn:e.learn,episode:true}))]) {
    const li = document.createElement("li"), btn = document.createElement("button");
    li.append(document.createTextNode(r.channel+" · "+r.value+(r.learn?" · learning":" · memory only")));
    btn.textContent="Forget"; btn.ariaLabel="Forget "+r.channel+" · "+r.value; btn.onclick=()=>forgetItem(r);
    li.append(btn); el("history").append(li);
  }
  reviewView(records,muted||[]);
  const summaries=Object.entries(prediction).map(([c,dist])=>{
    const values = Object.entries(dist);
    const best = Math.max(...values.map(x=>x[1]));
    return c+" · "+values.filter(x=>x[1]===best).map(x=>x[0]).join(" / ")+" ("+Math.round(100*best)+"%)";
  });
  el("forecast").textContent=summaries.length ? "Next observation estimate: "+summaries.join(", ") : "No learning observations retained.";
}
el("survey").onclick=()=>acquire("sound",false,true);
el("sound").onclick=()=>acquire("sound"); el("light").onclick=()=>acquire("light"); el("listen").onclick=()=>acquire("sound",true);
el("speak").onclick=spokenActivity;
el("forgetsession").onclick=forgetSession;
el("stop").onclick=()=>stop(); el("drop").onclick=()=>review(null);
document.querySelectorAll("[data-tag]").forEach(b=>b.onclick=()=>review(item("activity",b.dataset.tag)));
el("pairform").onsubmit=async e=>{
  e.preventDefault();
  try {
    const reply=await api("pair",{code:el("code").value.trim()});
    key=reply.key; shared.clear(); el("code").value=""; el("pairform").hidden=true; el("paired").hidden=false;
    el("pairhelp").textContent="This tab is paired. Shared observations expire after 24 hours.";
    el("linkstate").textContent="Paired with Spark"; buttons(); el("refresh").focus(); await historyView(); statusText("Paired.");
  } catch {statusText("Pairing or history unavailable. Check the code and connection.");}
};
el("save").onclick=async ()=>{
  if (!draft || sharing) return;
  const current=draft; sharing=true; buttons();
  try {
    await api("save",{...current,learn:el("learn").checked});
    if (draft===current) review(null);
    await historyView(); statusText("Shared.");
  } catch (e) {statusText(e.status===423 ? paused(current.channel) : "Could not confirm sharing. Refresh history before retrying.");}
  finally {sharing=false; buttons();}
};
el("refresh").onclick=()=>historyView().catch(()=>statusText("History unavailable."));
el("unpair").onclick=async ()=>{
  if (!confirm("Erase this pairing, its observations, and its learned counts?")) return;
  stop(); review(null);
  try {await api("unpair",{}); key=""; location.reload();} catch {statusText("Could not confirm erasure. Retry or erase the pairing on your Spark.");}
};
function privatePause() { stop(); review(null); el("handoff").textContent=""; }
document.addEventListener("visibilitychange",()=>{if (document.hidden) privatePause();});
window.addEventListener("pagehide",privatePause);

navigator.mediaDevices?.addEventListener?.("devicechange",()=>{if(selected) stop("Input devices changed");});

window.pocketBridge=api; window.pocketHistory=historyView; window.pocketSound=()=>["sound","dynamics"].includes(draft?.channel)?{[draft.channel]:[draft.value,"mic"]}:null;

el("linkdevice").onclick=async()=>{
  if(!confirm("Allow your other device to read and forget shared field episodes? Keep this code private."))return;
  try{const current=key;const reply=await api("link",{});if(document.hidden||key!==current)return;el("handoff").textContent="Enter on your other device within five minutes: "+reply.code;}catch{statusText("Link unavailable.");}
};


window.pocketToken=()=>key;
window.pocketSetPair=token=>{
 key=token; shared.clear();
 el("pairform").hidden=!!key;el("paired").hidden=!key;
 el("linkstate").textContent=key?"Paired with Spark":"Phone only";
 el("pairhelp").textContent=key?"This tab has a pairing credential. Refresh history to check connectivity.":"Enter your Spark or linked-device code.";
 if(!key){el("history").replaceChildren();el("forecast").textContent="";el("handoff").textContent="";}
 buttons();
};
