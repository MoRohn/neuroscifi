// Research node identity (ADR-042 draft; webcortex/signing.py is the Spark's side of every form below).
// This device's own signing key: WebCrypto Ed25519 where the browser has it, else ECDSA P-256 (detected at run time
// and recorded). The private key is generated NON-EXTRACTABLE and kept in IndexedDB, so it survives a reload without
// its material ever being readable by script; the pairing credential itself stays in tab memory (POC-07).
// The Spark signs every research reply with its Ed25519 key, pinned at pairing; verifySpark checks a reply before
// Pocket shows anything as "from your Spark" (WebCrypto Ed25519, or the small verifier below where it is missing).
export const ED25519="Ed25519", P256="ECDSA-P256";
const PARAMS={[ED25519]:{name:"Ed25519"},[P256]:{name:"ECDSA",namedCurve:"P-256"}};
const SIGN={[ED25519]:{name:"Ed25519"},[P256]:{name:"ECDSA",hash:"SHA-256"}};
const DB="nsf-node", STORE="keys", ID="identity", enc=new TextEncoder();

export const b64=bytes=>btoa(String.fromCharCode(...new Uint8Array(bytes))).replaceAll("+","-").replaceAll("/","_").replace(/=+$/,"");
export function unb64(text){
  if(typeof text!=="string"||!/^[A-Za-z0-9_-]+$/.test(text))throw Error("not base64url");
  const s=atob(text.replaceAll("-","+").replaceAll("_","/")+"===".slice((text.length+3)%4));
  return Uint8Array.from(s,c=>c.charCodeAt(0));
}
const hex=bytes=>[...new Uint8Array(bytes)].map(b=>b.toString(16).padStart(2,"0")).join("");
const concat=(...parts)=>{const out=new Uint8Array(parts.reduce((n,p)=>n+p.length,0));let i=0;for(const p of parts){out.set(p,i);i+=p.length;}return out;};
const sha256hex=async bytes=>hex(await crypto.subtle.digest("SHA-256",bytes));
export const nonce=()=>b64(crypto.getRandomValues(new Uint8Array(18)));

// The canonical forms: UTF-8, fields joined by "\n", each with its own tag.
export const canonicalChallenge=c=>enc.encode(["NSF-ENROL-1",c.challenge,c.family,c.node,String(c.expires)].join("\n"));
// A request names the family tag and node id it was signed for (node_enrol status), so it verifies for this device row
// only; NSF-REQ-1 lacked both and the Spark no longer accepts it (security review A-3).
export const canonicalRequest=async(family,node,method,path,body,ts,n)=>enc.encode(["NSF-REQ-2",family,node,method.toUpperCase(),path,await sha256hex(body),String(ts),n].join("\n"));
export const canonicalResponse=async(status,path,n,body)=>enc.encode(["NSF-RES-1",String(status),path,n||"",await sha256hex(body)].join("\n"));
export async function fingerprint(alg,raw){
  const d=hex(await crypto.subtle.digest("SHA-256",concat(enc.encode("NSF-KEY-1\n"+alg+"\n"),raw))).toUpperCase().slice(0,32);
  return d.match(/.{4}/g).join("-");
}

export const available=()=>!!(globalThis.crypto?.subtle&&globalThis.indexedDB);
// Ed25519 when this browser can generate it, else P-256 (Ed25519 is in recent Chromium, Firefox and Safari).
export async function algorithm(){
  try{await crypto.subtle.generateKey(PARAMS[ED25519],false,["sign","verify"]);return ED25519;}catch{return P256;}
}
function store(mode,act){
  return new Promise((done,fail)=>{
    const open=indexedDB.open(DB,1);
    open.onupgradeneeded=()=>open.result.createObjectStore(STORE);
    open.onerror=()=>fail(open.error);
    open.onsuccess=()=>{
      const db=open.result, t=db.transaction(STORE,mode), req=act(t.objectStore(STORE));
      t.oncomplete=()=>{db.close();done(req.result);};
      t.onerror=t.onabort=()=>{db.close();fail(t.error);};
    };
  });
}
let loading=null;
// {alg, privateKey (CryptoKey, not extractable), publicKey (base64url raw), fingerprint}, or null without a key.
export function identity(create=false){
  const run=async()=>{
    let rec=await store("readonly",s=>s.get(ID));
    if(!rec&&create){
      const alg=await algorithm();
      const pair=await crypto.subtle.generateKey(PARAMS[alg],false,["sign","verify"]);
      rec={alg,privateKey:pair.privateKey,publicKey:b64(await crypto.subtle.exportKey("raw",pair.publicKey)),created:Date.now()};
      await store("readwrite",s=>s.put(rec,ID));
    }
    return rec?{...rec,fingerprint:await fingerprint(rec.alg,unb64(rec.publicKey))}:null;
  };
  const next=(loading||Promise.resolve()).catch(()=>{}).then(run);   // one key, however many callers at once
  loading=next;
  return next;
}
export async function sign(bytes){
  const id=await identity();
  if(!id)throw Object.assign(Error("This device has no node key."),{code:"no_key"});
  return new Uint8Array(await crypto.subtle.sign(SIGN[id.alg],id.privateKey,bytes));
}
// The headers of a signed request besides its X-NSF-Nonce `n`; `who` is this device's node_enrol status ({family, node}).
export async function signedHeaders(who,path,body,n){
  if(typeof who?.family!=="string"||typeof who?.node!=="string")throw Object.assign(Error("This device's node id is unknown."),{code:"no_key"});
  const ts=Math.floor(Date.now()/1000);
  return {"X-NSF-Timestamp":String(ts),"X-NSF-Signature":b64(await sign(await canonicalRequest(who.family,who.node,"POST",path,body,ts,n)))};
}

// ---- the Spark's signed replies --------------------------------------------------------------------------------
// True only when `sig` is the pinned Spark key's Ed25519 signature over this status, path, request nonce and body.
export async function verifySpark(pin,status,path,n,body,sig){
  if(!pin||pin.alg!==ED25519||!sig)return false;
  let key,signature;
  try{key=unb64(pin.key);signature=unb64(sig);}catch{return false;}
  if(key.length!==32||signature.length!==64)return false;
  const message=await canonicalResponse(status,path,n,body);
  let k;
  try{k=await crypto.subtle.importKey("raw",key,{name:"Ed25519"},false,["verify"]);}
  catch{return ed25519Verify(key,signature,message);}     // this browser has no WebCrypto Ed25519
  return crypto.subtle.verify({name:"Ed25519"},k,signature,message);
}

// RFC 8032 Ed25519 verification ([S]B = R + [k]A, encodings compared), for browsers without WebCrypto Ed25519.
// Public data only, so timing does not matter; SHA-512 comes from WebCrypto.
const P=2n**255n-19n, L=2n**252n+27742317777372353535851937790883648493n;
const mod=(a,m=P)=>{const r=a%m;return r>=0n?r:r+m;};
function pow(b,e){let r=1n;b=mod(b);while(e>0n){if(e&1n)r=r*b%P;b=b*b%P;e>>=1n;}return r;}
const D=mod(-121665n*pow(121666n,P-2n)), SQRT_M1=pow(2n,(P-1n)/4n);
const BASE=[15112221349535400772501151409588531511454012693041857206046113283949847762202n,
  46316835694926478169428394003475163141307993866256225615783033603165251855960n,1n];
BASE.push(mod(BASE[0]*BASE[1]));
function add([X1,Y1,Z1,T1],[X2,Y2,Z2,T2]){   // extended coordinates, a = -1; complete, so it also doubles
  const A=mod((Y1-X1)*(Y2-X2)), B=mod((Y1+X1)*(Y2+X2)), C=mod(2n*D*T1*T2), Z=mod(2n*Z1*Z2);
  const E=B-A, F=Z-C, G=Z+C, H=B+A;
  return [mod(E*F),mod(G*H),mod(F*G),mod(E*H)];
}
function mul(point,n){let r=[0n,1n,1n,0n];for(let q=point;n>0n;n>>=1n,q=add(q,q))if(n&1n)r=add(r,q);return r;}
const le=bytes=>bytes.reduceRight((v,b)=>(v<<8n)|BigInt(b),0n);
function decode(bytes){
  const sign=bytes[31]>>7, y=le(Uint8Array.from(bytes,(b,i)=>i===31?b&0x7f:b));
  if(y>=P)return null;
  const u=mod(y*y-1n), v=mod(D*y*y+1n);
  let x=mod(u*pow(v,3n)*pow(u*pow(v,7n),(P-5n)/8n));
  const vx2=mod(v*x*x);
  if(vx2!==u){if(vx2!==mod(-u))return null;x=mod(x*SQRT_M1);}
  if(x===0n&&sign)return null;
  if(Number(x&1n)!==sign)x=mod(-x);
  return [x,y,1n,mod(x*y)];
}
function encode([X,Y,Z]){
  const zi=pow(Z,P-2n), x=mod(X*zi);let y=mod(Y*zi);
  const out=new Uint8Array(32);
  for(let i=0;i<32;i++){out[i]=Number(y&255n);y>>=8n;}
  if(x&1n)out[31]|=0x80;
  return out;
}
export async function ed25519Verify(pub,sig,message){
  const A=decode(pub), R=decode(sig.subarray(0,32)), s=le(sig.subarray(32));
  if(!A||!R||s>=L)return false;
  const k=le(new Uint8Array(await crypto.subtle.digest("SHA-512",concat(sig.subarray(0,32),pub,message))))%L;
  const left=encode(mul(BASE,s)), right=encode(add(R,mul(A,k)));
  return left.every((b,i)=>b===right[i]);
}
