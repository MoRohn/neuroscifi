// Pocket's outbox (ENGINEERING_PLAN.md §6 item 12): a research question written while your Spark is out of reach
// waits on this device and is sent once the Spark answers. Each question carries a request key made here (a UUID v4)
// that the Spark keeps with the task it queues, so sending it again after a lost reply never queues it twice
// (pocket/server.py research_submit). The key goes inside the request body, so a research node's signature covers
// it; a waiting question is signed again each time it is sent, never with an old signature.
// Storage: inside the encrypted device vault when it is open (it then survives a reload), else in this tab only.
// Nothing here talks to the network or the DOM: research-ui.mjs sends, stores and draws.
export const OUTBOX_DAYS=7, MAX_WAITING=10;
const DAY=86400000;

// A waiting question in the vault's row shape (field-model.mjs liveRows keeps a "week" row seven days).
export function entry(data,spark,now=Date.now(),id=crypto.randomUUID()){
  const question={objective:data.objective,state:"waiting",note:"",spark:spark?.key||null,tries:0};
  if(data.mode)question.mode=data.mode;
  return {id,created:now,keep:"week",outbox:question};
}
export const waiting=rows=>rows.filter(r=>r?.outbox);
// What is sent: the question as it was written, with its key. Nothing else from the device.
export function request(item){
  const data={objective:item.outbox.objective};
  if(item.outbox.mode)data.mode=item.outbox.mode;
  data.request=item.id;
  return data;
}
// Questions that may be sent now to the Spark whose key is pinned, oldest first. A question written for another
// Spark (an earlier pairing) is never sent to this one.
export function due(rows,spark){
  return waiting(rows).filter(r=>r.outbox.state==="waiting"&&r.outbox.spark===(spark?.key||null))
    .sort((a,b)=>a.created-b.created||a.id.localeCompare(b.id));
}
// What a reply means for a waiting question:
//   sent     the Spark queued it now (201) or had queued it before (200, replayed): remove it here
//   gone     the Spark had queued it and the owner forgot that task since (410): remove it and say so
//   refused  the Spark will never take it as written (400: private details, too long): keep it, ask the owner
//   later    anything else (unreachable, an unverified reply, the hourly limit or daily budget, paused storage,
//            research off, a pairing or node refusal): keep it and stop sending until later
export function outcome(error){
  if(!error)return "sent";
  if(error.status===410)return "gone";
  if(error.status===400)return "refused";
  return "later";
}
// Seconds until the next automatic try after `tries` failed rounds: 15 s doubling to at most 10 minutes.
export const backoff=tries=>Math.min(600,15*2**Math.max(0,Math.min(tries,6)));
// The day a question stops being kept (seven days after it was written).
export const until=item=>item.created+OUTBOX_DAYS*DAY;
// Room for one more waiting question.
export const full=rows=>waiting(rows).length>=MAX_WAITING;
export function replace(rows,id,patch){
  return rows.map(r=>r.id===id&&r.outbox?{...r,outbox:{...r.outbox,...patch}}:r);
}
