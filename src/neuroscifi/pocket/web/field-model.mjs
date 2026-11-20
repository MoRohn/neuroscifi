// Fixed categories are the only features eligible for the episodic learner.
export const fields = {
  goal:["work","read","unwind","move"], sound:["soft","moderate","strong"],
  dynamics:["steady","variable","wide"], visible:["brief","several","extended"], layout:["low","medium","high"],
  switches:["few","some","many"], wakes:["few","some","many"],
  screenTime:["under5","5to20","20plus"], appTime:["under5","5to20","20plus"], power:["saving","standard"], thermal:["normal","elevated","high"]
};
export const origins = {goal:["owner"], sound:["mic"], dynamics:["mic"], visible:["pocket"],
  layout:["image"], switches:["android","shortcut"], wakes:["android"],
  screenTime:["android"],appTime:["android"],power:["android","ios"], thermal:["android","ios"]};
export const outcomes=["continued","switched","paused"];
export function checkFeatures(features) {
  if (!features || typeof features!=="object" || Array.isArray(features)) throw Error("Invalid features");
  for (const [k,pair] of Object.entries(features)) {
    if (!Object.hasOwn(fields,k) || !Array.isArray(pair) || pair.length!==2 ||
        !fields[k].includes(pair[0]) || !origins[k].includes(pair[1])) throw Error("Unsupported observation");
  }
  return features;
}
export function deviceReceipt(x) {
  if (!x || Object.keys(x).sort().join()!=="features,schema" || x.schema!=="pocket-device-1") throw Error("Unknown receipt format");
  const entries=Object.entries(checkFeatures(x.features));
  if(!entries.length || entries.some(([k,p])=>!["switches","wakes","screenTime","appTime","power","thermal"].includes(k) ||
    !["android","ios","shortcut"].includes(p[1]))) throw Error("Unexpected device field");
  return x.features;
}
export function forecast(rows, features) {
  checkFeatures(features);
  // Each episode contributes one vote, even when several readings match.
  const ranked=[];
  for (const r of rows) {
    if (!r.learn || !outcomes.includes(r.outcome)) continue;
    let same=0,compared=0;
    for(const [k,[v,source]] of Object.entries(features)) {
      const p=r.features[k]; if(!p || p[1]!==source) continue;
      compared++; if(p[0]===v) same++;
    }
    if(same && same/compared>=.67) ranked.push({r,score:same/compared*Math.min(1,compared/3)});
  }
  ranked.sort((a,b)=>b.score-a.score || b.r.created-a.r.created || a.r.id.localeCompare(b.r.id));
  const neighbors=ranked.slice(0,12), weights=Object.fromEntries(outcomes.map(v=>[v,1]));
  let support=0;
  for(const {r,score} of neighbors){weights[r.outcome]+=score;support+=score;}
  const probs=Object.fromEntries(outcomes.map(v=>[v,weights[v]/(3+support)]));
  const top=outcomes.reduce((a,b)=>probs[a]>=probs[b]?a:b);
  return {probs,answer:support>=3 && probs[top]>=.55?top:null,ids:neighbors.map(x=>x.r.id),support};
}
export function texture(rgba,width=32) {
  let diff=0,n=0;
  for(let i=4;i<rgba.length;i+=4) if((i/4)%width) {
    for(let c=0;c<3;c++)diff+=Math.abs(rgba[i+c]-rgba[i-4+c]);
    n+=3;
  }
  const mean=diff/Math.max(1,n);return mean<9?"low":mean<27?"medium":"high";
}
export const liveRows=(rows,now=Date.now())=>rows.filter(r=>r.created<=now && r.created>now-86400000*(r.keep==="week"?7:1));
const b64=b=>btoa(String.fromCharCode(...new Uint8Array(b)));
const bytes=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
export async function vaultKey(pass,salt) {
  const base=await crypto.subtle.importKey("raw",new TextEncoder().encode(pass),"PBKDF2",false,["deriveKey"]);
  return crypto.subtle.deriveKey({name:"PBKDF2",salt:bytes(salt),iterations:600000,hash:"SHA-256"},
      base,{name:"AES-GCM",length:256},false,["encrypt","decrypt"]);
}
export const newSalt=()=>b64(crypto.getRandomValues(new Uint8Array(16)));
export async function seal(rows,key) {
  const iv=crypto.getRandomValues(new Uint8Array(12));
  const body=await crypto.subtle.encrypt({name:"AES-GCM",iv},key,new TextEncoder().encode(JSON.stringify(rows)));
  return {iv:b64(iv),body:b64(body)};
}
export async function openBox(box,key) {
  const content=await crypto.subtle.decrypt({name:"AES-GCM",iv:bytes(box.iv)},key,bytes(box.body));
  return liveRows(JSON.parse(new TextDecoder().decode(content)));
}
