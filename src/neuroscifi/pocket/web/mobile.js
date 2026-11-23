"use strict";
const el = id => document.getElementById(id);
let key = "", draft = null, selected = null, generation = 0, sharing = false, lastActive = 0;
let reach = "", refused = false, muting = false, pairingNow = false, messageTimer = 0;
let probeTimer = 0, probeDelay = 0, restoring = false;
// A pairing invitation from a QR code (ADR-043): https://<spark>/#pair=<code> from the Spark's "Add a phone" screen.
// The fragment never reaches a server. It is read once and removed from the address bar and this tab's history at
// once (the browser's own history may still list the visited link; the code works once, for 5 minutes), and used only
// when the person taps Pair, with "via": "invitation" so the Spark accepts only its own invitation from a link, never
// a linked device's handoff code (a link cannot put this device into someone else's family).
const INVITED = "This device was invited to pair with your Spark. Tap Pair to finish.";
let invited = (() => {
  const hash = globalThis.location?.hash ?? "";
  if (!/^#pair=/.test(hash)) return null;
  const m = /^#pair=([A-Za-z0-9_-]{16,64})$/.exec(hash);
  globalThis.history?.replaceState?.(null, "", location.pathname + location.search);
  return m ? m[1] : null;
})();
// The Spark's signing key as pinned at pairing (ADR-042 draft): kept with the credential, in tab memory or the vault.
// research-ui.mjs checks every research reply against it before showing anything as from your Spark.
let spark = null;
function showPin() {   // a bridge without research pins nothing; research-ui.mjs explains a missing pin where it matters
  el("sparkprint").textContent = key && spark ? "Your Spark's key fingerprint, pinned when this device paired: " + spark.fingerprint : "";
  el("sparkprint").hidden = !el("sparkprint").textContent;
}
const shared = new Map();  // ids this tab shared since pairing: memory only, never sent anywhere
const guards = new Set();  // one-shot actions in flight (double-tap guard), released shortly after they finish
const paused = channel => "Learning is paused for " + channel + ". Resume it under Evening review, or share without learning.";
const UNREACHABLE = "Your Spark is unreachable. Check that this device is on the same network and Pocket is running on your Spark.";
// One status line (U-09): plain confirmations (`transient`) clear themselves after 8 seconds; errors, stops and refusals
// stay until Dismiss (RT-B-02) so the explanation is still there when the person looks back.
const statusText = (text, transient = false) => {
  el("message").textContent = text;
  clearTimeout(messageTimer);
  if (text && transient) messageTimer = setTimeout(() => { if (el("message").textContent === text) el("message").textContent = ""; }, 8000);
};
// Focus moves only when the control that had it was disabled or hidden by the action (WCAG 2.4.3).
function rescue(target) {
  const active = document.activeElement;
  if (!active || active === document.body || active.disabled || active.checkVisibility?.() === false) target.focus?.();
}
async function once(name, action) {
  if (guards.has(name)) return;
  guards.add(name);
  try { return await action(); } finally { setTimeout(() => guards.delete(name), 600); }
}
// The header tells the truth about the Spark: paired and answering, unreachable, offline, or refused (U-19).
function linkText() {
  const offline = navigator.onLine === false;
  el("linkstate").textContent = !key ? (refused ? "Not paired · pair again" : "Phone only") + (offline ? " · offline" : "")
    : offline ? "Paired · this device is offline" : reach === "checking" ? "Checking your Spark…"
    : reach === "down" ? "Paired · Spark unreachable" : "Paired with Spark";
}
// Paired but unreachable (a bridge restart, a dropped request): check again with backoff (1 s doubling to 30 s) until
// the Spark answers, then reload state so the header, Research and Voice come back without a reload (R2-01). A reload
// still forgets a pairing kept only in tab memory (POC-07); one kept in the vault is restored and re-checked.
function reached(ok) {
  if (!key) return;
  const next = ok ? "ok" : "down", was = reach;
  if (reach !== next) { reach = next; linkText(); }
  if (!ok) { if (!probeTimer) reprobeLater(); return; }
  clearTimeout(probeTimer); probeTimer = 0; probeDelay = 0;
  if (was === "down") restore();
}
function reprobeLater() {
  probeDelay = Math.min(30000, probeDelay ? probeDelay * 2 : 1000);
  probeTimer = setTimeout(() => { probeTimer = 0; if (key && reach === "down") restore(); }, probeDelay);
}
function restore() {
  if (restoring) return;
  restoring = true;
  historyView().then(() => {   // api() records the outcome via reached(); a vault pairing is now confirmed too
    if (el("pairhelp").textContent.startsWith("Your Spark did not answer")) el("pairhelp").textContent = "This tab is paired from your vault. Shared observations expire after 24 hours.";
  }, () => {}).finally(() => { restoring = false; });
}
function stopProbing() { clearTimeout(probeTimer); probeTimer = 0; probeDelay = 0; }
// A 403 to a credential this tab holds: the Spark erased the pairing. Say so, forget it here and in the vault.
function revoked() {
  if (!key) return;
  key = ""; spark = null; refused = true; shared.clear(); stopProbing(); showPin();
  el("pairform").hidden = false; el("paired").hidden = true;
  el("pairhelp").textContent = "Your Spark no longer accepts this pairing. Enter a new code from your Spark or a linked device.";
  el("history").replaceChildren(); el("forecast").textContent = ""; el("handoff").textContent = "";
  linkText(); statusText("Your Spark no longer accepts this pairing. Pair again.");
  window.pocketDropPair?.(); window.pocketPushDrop?.();
  pairing(false); buttons();
}

async function api(route, data) {
  const headers = {"Content-Type": "application/json"}, sent = key;
  if (key) headers.Authorization = "Bearer " + key;
  let response;
  try {
    response = await fetch("/api/" + route, {method:"POST", headers, body:JSON.stringify(data), cache:"no-store", credentials:"omit"});
  } catch {
    if (sent && sent === key) reached(false);
    throw Object.assign(Error("Spark unreachable"), {status: 0});
  }
  if (sent && sent === key) {
    if (response.status === 403 && route !== "pair") { revoked(); throw Object.assign(Error("Pairing refused"), {status: 403, revoked: true}); }
    reached(true);
  }
  if (!response.ok) {
    const body = await Promise.resolve(response.json?.()).catch(() => ({}));
    throw Object.assign(Error("Spark returned " + response.status), {status: response.status, code: body?.error});
  }
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
  const nudge = el("pairnudge");                  // unpaired: say where pairing starts, at the top of the page
  if (nudge) nudge.hidden = !!key || !!invited;
}
function sensorTime() {
  el("lastactive").textContent = el("sensor").dataset.live === "yes" ? "active now"
    : lastActive ? new Date(lastActive).toLocaleTimeString([], {hour:"numeric", minute:"2-digit"}) : "none since this page opened";
}
// Tells the Research panel whether this pairing may research (the bridge says so in `state` only when enabled).
function pairing(research, push = false) { document.dispatchEvent?.(new CustomEvent("pocketpair", {detail:{paired:!!key, research, push}})); }
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
  if (old) rescue(draft ? el("draft") : el(old.origin || "sound"));
}
async function acquire(kind, timed = false, survey = false) {
  if (selected || document.hidden) return;
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    statusText("Open this page over trusted HTTPS, or use localhost on the same device."); return;
  }
  const serial = ++generation;
  const run = {auto:timed && !!key && el("auto").checked, pending:false, origin:timed ? "listen" : survey ? "survey" : kind};
  selected = run; buttons(); rescue(el("stop"));
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
        catch (e) { if (generation===serial) { stop(); if (!e.revoked) statusText(e.status===423 ? paused("sound")+" Automatic sharing stopped." : e.status===0 ? UNREACHABLE+" Automatic sharing stopped." : "Sync interrupted. Check history before restarting."); } }
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
  const serial = ++generation, run = {origin:"speak"};
  selected = run; buttons(); rescue(el("stop"));
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
          review(item("activity",label)); statusText("Review the suggested activity before sharing.", true);
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
function failure(e, text) {
  if (!e.revoked) statusText(e.status === 0 ? UNREACHABLE + " " + text : e.status === 507 ? "Your Spark has paused new writes because its storage needs attention. " + text : text);
}
async function forgetItem(r) {
  try {await api(r.episode?"forget_episode":"forget",{id:r.id}); await historyView(); el("refresh").focus();} catch (e) {failure(e, "Could not confirm deletion. Refresh and retry.");}
}
let patternButtons = new Map();
async function setMute(channel, on) {
  if (muting) return;  // a double click sends one request and reports its count (P-05)
  muting = true; for (const b of patternButtons.values()) b.disabled = true;
  try {
    const reply = await api(on ? "mute" : "unmute", {channel});
    await historyView();
    const n = reply.unlearned;
    statusText(on ? "Learning paused for "+channel+". "+(n === 1 ? "1 observation no longer changes" : n+" observations no longer change")+" the forecast."
      : "Learning resumed for "+channel+". New observations can opt in again.");
  } catch (e) {failure(e, "Could not confirm the change. Refresh and retry.");}
  finally {
    muting = false; for (const b of patternButtons.values()) b.disabled = false;
    (patternButtons.get(channel) || el("refresh")).focus?.();
  }
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
  patternButtons = new Map();
  for (const channel of [...new Set([...Object.keys(counts), ...muted])].sort()) {
    const on = !muted.includes(channel), li = document.createElement("li"), btn = document.createElement("button");
    li.append(document.createTextNode(channel+(on ? " · "+counts[channel]+" learning" : " · learning paused")));
    btn.textContent = on ? "Stop learning this pattern" : "Resume learning"; btn.ariaLabel = btn.textContent+": "+channel;
    btn.disabled = muting; btn.onclick = () => setMute(channel, on); patternButtons.set(channel, btn); li.append(btn); el("patterns").append(li);
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
  const {records,prediction,muted,research,push} = await api("state",{});
  const {episodes} = await api("episodes",{});
  if (requestedKey!==key || document.hidden) return;
  pairing(research === true, push === true);
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
el("stop").onclick=()=>stop(); el("drop").onclick=()=>{review(null); rescue(el("draft"));};
document.querySelectorAll("[data-tag]").forEach(b=>b.onclick=()=>review(item("activity",b.dataset.tag)));
// Wrong, expired or reused codes are told apart from a Spark that cannot be reached (UX 1, U-20).
function pairFailure(e) {
  return e.status === 0 ? "Could not reach your Spark. Check that this device is on the same network as your Spark and that Pocket is running there, then try again."
    : e.status === 409 && e.code === "family_full" ? "That pairing already has 6 devices. Forget one of them (on that device, or on your Spark), then ask for a new code."
    : e.status === 403 || e.status === 400 ? "That code was not accepted. Codes work once and expire after 5 minutes. Check it, or get a new code from your Spark or a linked device."
    : "Your Spark could not pair right now (error " + e.status + "). Try again in a moment.";
}
el("pairform").onsubmit=async e=>{
  e.preventDefault();
  if (pairingNow) return;
  pairingNow=true;
  try {
    let reply;
    const code=el("code").value.replace(/\s+/g,"");   // codes have no spaces; typed groups may
    try { reply=await api("pair",invited&&code===invited?{code,via:"invitation"}:{code}); }
    catch (err) { statusText(pairFailure(err)); if (invited) { invited=null; el("code").value=""; el("spark").classList?.remove("invited"); el("pairhelp").textContent="That invitation did not work. Scan the newest code on your Spark's screen, or enter a code."; } el("code").focus?.(); return; }
    key=reply.key; spark=reply.spark||null; refused=false; reach="ok"; stopProbing(); shared.clear(); el("code").value=""; el("pairform").hidden=true; el("paired").hidden=false; showPin();
    invited=null; el("spark").classList?.remove("invited");
    el("pairhelp").textContent="This tab is paired. Shared observations expire after 24 hours.";
    linkText(); buttons(); el("refresh").focus();
    try { await historyView(); statusText("Paired.", true); }
    catch (err) { failure(err, "Paired, but history is unavailable right now. Use Refresh history."); }
  } finally { pairingNow=false; }
};
el("save").onclick=async ()=>{
  if (!draft || sharing) return;
  const current=draft; sharing=true; buttons();
  try {
    await api("save",{...current,learn:el("learn").checked});
    if (draft===current) review(null);
    await historyView(); statusText("Shared.", true);
  } catch (e) {failure(e, e.status===423 ? paused(current.channel) : e.status===0 ? "Nothing was shared; the observation is still waiting." : "Could not confirm sharing. Refresh history before retrying.");}
  finally {sharing=false; buttons(); rescue(draft ? el("save") : el("draft"));}
};
el("refresh").onclick=()=>historyView().catch(e=>failure(e, "History will load when it answers."));
async function forgetPairing(family) {
  if (!confirm(family ? "Erase this pairing and every phone or tablet linked with it? Each loses access and its records on your Spark."
    : "Erase this pairing, its observations, and its learned counts?")) return;
  stop(); review(null);
  try {
    await api("unpair", family ? {family:true} : {});
    key=""; await window.pocketDropPair?.(); await window.pocketPushDrop?.(); location.reload();
  } catch (e) {failure(e, "Could not confirm erasure. Retry or erase the pairing on your Spark.");}
}
el("unpair").onclick=()=>once("unpair", ()=>forgetPairing(false));
el("unpairall").onclick=()=>once("unpair", ()=>forgetPairing(true));
el("dismiss").onclick=()=>{statusText(""); el("main").focus?.();};
// Back online: whatever failed while offline said nothing about the Spark, so check it again instead of keeping a
// stale "Spark unreachable" until some unrelated request happens to succeed.
function backOnline() {
  if (!key) { linkText(); return; }
  reach = "checking"; linkText();
  api("state", {}).catch(() => {});  // api() records the outcome through reached() / revoked()
}
window.addEventListener("online", backOnline); window.addEventListener("offline", linkText);
function privatePause() { stop(); review(null); el("handoff").textContent=""; }
document.addEventListener("visibilitychange",()=>{if (document.hidden) privatePause();});
window.addEventListener("pagehide",privatePause);

navigator.mediaDevices?.addEventListener?.("devicechange",()=>{if(selected) stop("Input devices changed");});

window.pocketBridge=api; window.pocketHistory=historyView; window.pocketSound=()=>["sound","dynamics"].includes(draft?.channel)?{[draft.channel]:[draft.value,"mic"]}:null;

// One dialog and one code per tap, however fast the taps (UX 10).
el("linkdevice").onclick=()=>once("link", async()=>{
  if(!confirm("Allow your other device to read and forget shared field episodes? Keep this code private."))return;
  try{const current=key;const reply=await api("link",{});if(document.hidden||key!==current)return;const text="Enter on your other device within five minutes: "+reply.code;el("handoff").textContent=text;
    // The code stops working after five minutes: stop showing it then (RT-B-08).
    setTimeout(()=>{if(el("handoff").textContent===text)el("handoff").textContent="";},300000);}
  catch(e){failure(e, e.code==="family_full" ? "This pairing already has 6 devices. Forget one before linking another." : "Link unavailable.");}
});


window.pocketToken=()=>key;
window.pocketSpark=()=>key?spark:null;
window.pocketReach=reached; window.pocketRefused=revoked;
// A credential restored from the vault is checked with the Spark before the header says "Paired" (P-02).
window.pocketSetPair=(token,pin=null)=>{
 key=token; spark=key&&pin?.alg?pin:null; shared.clear(); refused=false; reach=key?"checking":""; stopProbing(); showPin();
 el("pairform").hidden=!!key;el("paired").hidden=!key;
 linkText();
 el("pairhelp").textContent=key?"Checking the pairing kept in your vault with your Spark…":invited?INVITED:"Enter your Spark or linked-device code.";
 if(!key){el("history").replaceChildren();el("forecast").textContent="";el("handoff").textContent="";}
 pairing(false);
 buttons();
 if(!key)return Promise.resolve(false);
 return historyView().then(()=>{el("pairhelp").textContent="This tab is paired from your vault. Shared observations expire after 24 hours.";return true;},
   e=>{if(e.revoked)return false;el("pairhelp").textContent="Your Spark did not answer, so this pairing is not confirmed yet. Use Refresh history when it is reachable.";return null;});
};
// The invitation (see INVITED): fill the code, bring the pairing card into view and wait for a tap.
if (invited && !key) {
  el("code").value = invited;
  el("pairhelp").textContent = INVITED;
  el("spark").classList?.add("invited");
  el("spark").scrollIntoView?.({block: "center"});
  el("pairform").querySelector?.("button")?.focus?.({preventScroll: true});
}
buttons();
