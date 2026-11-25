// Pocket's outbox module (src/neuroscifi/pocket/web/outbox.mjs): what waits, what is sent, what a reply means.
import assert from "node:assert/strict";
import * as outbox from "../../src/neuroscifi/pocket/web/outbox.mjs";
import {liveRows} from "../../src/neuroscifi/pocket/web/field-model.mjs";

const spark={alg:"Ed25519",key:"spark-key-A"}, other={alg:"Ed25519",key:"spark-key-B"}, now=Date.UTC(2026,10,24,12);
const a=outbox.entry({objective:"What is the latest Zephyr compiler?"},spark,now,"6a3c0e8e-2d6c-4f7e-9d47-1f0d2c9b8e11");
const b=outbox.entry({objective:"Compare two compilers",mode:"comparative"},spark,now-5000,"7b3c0e8e-2d6c-4f7e-9d47-1f0d2c9b8e12");
const c=outbox.entry({objective:"For the old Spark"},other,now-9000,"8c3c0e8e-2d6c-4f7e-9d47-1f0d2c9b8e13");

// A waiting question is a vault row kept a week; a fresh key is a UUID v4 made on the device.
assert.equal(a.keep,"week");assert.equal(a.outbox.state,"waiting");assert.equal(a.outbox.spark,"spark-key-A");
assert.match(outbox.entry({objective:"x"},spark).id,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
assert.notEqual(outbox.entry({objective:"x"},spark).id,outbox.entry({objective:"x"},spark).id);

// What is sent: the question as written and its key, nothing else (no Spark key, no note, no count).
assert.deepEqual(outbox.request(a),{objective:"What is the latest Zephyr compiler?",request:a.id});
assert.deepEqual(outbox.request(b),{objective:"Compare two compilers",mode:"comparative",request:b.id});

// Due: oldest first, only for the pinned Spark, only those still waiting; field rows are ignored.
const field={id:"f",created:now,features:{goal:["work","owner"]},outcome:"continued"};
const rows=[field,a,b,c];
assert.deepEqual(outbox.due(rows,spark).map(r=>r.id),[b.id,a.id]);
assert.deepEqual(outbox.due(rows,other).map(r=>r.id),[c.id]);
assert.deepEqual(outbox.due(rows,null),[],"without a pinned Spark nothing is sent");
const refused=outbox.replace(rows,b.id,{state:"refused",note:"Not sent"});
assert.deepEqual(outbox.due(refused,spark).map(r=>r.id),[a.id]);
assert.equal(refused[0],field,"replace leaves other rows untouched");

// What a reply means.
assert.equal(outbox.outcome(null),"sent");
assert.equal(outbox.outcome({status:410,code:"forgotten"}),"gone");
assert.equal(outbox.outcome({status:400,code:"private_data"}),"refused");
assert.equal(outbox.outcome({status:400}),"refused");
for(const e of [{status:0},{status:200,code:"unverified"},{status:429},{status:429,code:"daily_budget"},{status:507},
  {status:404},{status:403},{status:401,code:"stale_request"},{status:500},{status:0,code:"not_paired"}])
  assert.equal(outbox.outcome(e),"later",JSON.stringify(e));

// Bounds: ten waiting at most; kept seven days (liveRows drops an older row), backoff 15 s to 10 minutes.
assert.equal(outbox.full(Array.from({length:9},(_,i)=>outbox.entry({objective:"q"+i},spark))),false);
assert.equal(outbox.full(Array.from({length:10},(_,i)=>outbox.entry({objective:"q"+i},spark))),true);
assert.equal(outbox.until(a)-a.created,7*86400000);
assert.deepEqual(liveRows([a],now+7*86400000-1).map(r=>r.id),[a.id]);
assert.deepEqual(liveRows([a],now+7*86400000+1),[]);
assert.deepEqual([0,1,2,3,4,5,6,9].map(outbox.backoff),[15,30,60,120,240,480,600,600]);
console.log("Passed outbox: keys, request body, due order and Spark binding, reply outcomes, bounds and backoff.");
