"""NeuroSciFi calibration bench. Run with Python and NumPy on any CPU."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

STATES = np.array([[w, s, t] for w in range(2) for s in range(2) for t in range(2)], dtype=np.int8)
CAMERA = STATES[:, 0] ^ STATES[:, 1]
SPEAKER = STATES[:, 0] ^ STATES[:, 2]
BODY = STATES[:, 0]
SENSOR = STATES[:, 1]
VOCAB = STATES[:, 2]


def probabilities(log_scores):
    scaled = np.exp(log_scores - log_scores.max(axis=1, keepdims=True))
    return scaled / scaled.sum(axis=1, keepdims=True)


def update(log_scores, readout, observations, error):
    """Update event likelihood. Each row is one learner."""
    log_scores += np.where(readout[None, :] == observations[:, None], np.log1p(-error), np.log(error))


def metrics(scores, true_ids):
    prob = probabilities(scores)
    rows = np.arange(len(true_ids))
    winners = np.isclose(prob, prob.max(axis=1, keepdims=True), atol=1e-14, rtol=1e-12)
    return {
        "exact_state_accuracy_ties_split": float((winners[rows,true_ids] / winners.sum(axis=1)).mean()),
        "multiclass_brier_sum": float((1 - 2*prob[rows,true_ids] + (prob**2).sum(axis=1)).mean()),
        "mean_confidence": float(prob.max(axis=1).mean()),
        "mean_probability_on_truth": float(prob[rows,true_ids].mean()),
    }


def balanced_ids(count, rng):
    if count % 8:
        raise ValueError("World count must be divisible by eight")
    return rng.permutation(np.tile(np.arange(8),count//8))


def receive(bits, noise, rng):
    return bits ^ (rng.random(len(bits)) < noise)


def static_bench(n, seed, error=0.10):
    rng = np.random.default_rng(seed)
    truth = balanced_ids(n,rng)
    scores = np.zeros((n,8))
    for _ in range(12):
        update(scores,CAMERA,receive(CAMERA[truth],error,rng),error)
        update(scores,SPEAKER,receive(SPEAKER[truth],error,rng),error)
    result = {"passive":metrics(scores,truth)}
    heldout = {}
    p = probabilities(scores)
    for name, channel in [("camera",CAMERA),("speech",SPEAKER)]:
        forecast = ((p @ channel) > .5).astype(np.int8)
        heldout[name] = float((forecast == receive(channel[truth],error,rng)).mean())
    result["fresh_noisy_report_accuracy"] = heldout
    initial = scores.copy()
    root_fault = rng.random(n) < .15
    noise = rng.random((5,n)) < error
    variants = {"independent_trials":(scores.copy(),0),
                "shared_standard_fault":(scores.copy(),1),
                "two_standards":(scores.copy(),2)}
    for trial in range(5):
        for name,(current,mode) in variants.items():
            # In mode 2, the last three trials use a separately keyed, healthy standard.
            faulty = root_fault if (mode==1 or (mode==2 and trial<2)) else False
            obs = BODY[truth] ^ faulty ^ noise[trial]
            update(current,BODY,obs,error)
            if trial in (0,2,4):
                result[f"{name}_{trial+1}"] = metrics(current,truth)
    single = receive(BODY[truth],error,rng)
    for copies in [1,4,16]:
        copied = initial.copy()
        for _ in range(copies):
            update(copied,BODY,single,error)
        result[f"one_event_counted_{copies}"] = metrics(copied,truth)
    return result


def drift_transition(hazards):
    changes = STATES[:,None,:] != STATES[None,:,:]
    return np.prod(np.where(changes,hazards,1-np.array(hazards)),axis=2)


def streaming_bench(n, seed, ticks=192, sensor_noise=.1):
    """Filter knows the event law. Each policy sees the same paired worlds and potential readings."""
    rng=np.random.default_rng(seed)
    hazards=np.array([.018,.009,.014])
    trans=drift_transition(hazards)
    true_ids=balanced_ids(n,rng)
    names=["passive","periodic","uncertainty","random"]
    post={k:np.full((n,8),1/8.) for k in names}
    used={k:np.zeros(n,dtype=int) for k in names}
    acc={k:np.zeros(n) for k in names}
    score={k:np.zeros(n) for k in names}
    final_counts={k:np.zeros(n,dtype=int) for k in names}
    rows=np.arange(n)
    stride=8
    # Every policy except passive gets exactly one opportunity per eight-step block.
    random_slot=rng.integers(0,stride,size=(ticks//stride,n))
    for tick in range(ticks):
        if tick:
            bits=STATES[true_ids] ^ (rng.random((n,3))<hazards)
            true_ids=bits @ np.array([4,2,1])
        cam=receive(CAMERA[true_ids],sensor_noise,rng)
        voice=receive(SPEAKER[true_ids],sensor_noise,rng)
        standard=receive(BODY[true_ids],sensor_noise,rng)
        slot=tick%stride
        if slot==0:
            for k in names:
                used[k].fill(0)
        for k in names:
            logp=np.log(post[k] @ trans) if tick else np.log(post[k])
            update(logp,CAMERA,cam,sensor_noise)
            update(logp,SPEAKER,voice,sensor_noise)
            p=probabilities(logp)
            if k=="periodic":
                ask=np.full(n,slot==0)
            elif k=="random":
                ask=(random_slot[tick//stride]==slot)
            elif k=="uncertainty":
                body_p=p @ BODY
                ask=(used[k]==0) & ((np.abs(body_p-.5)<.25) | (slot==stride-1))
            else:
                ask=np.zeros(n,dtype=bool)
            if ask.any():
                upd=logp[ask].copy()
                update(upd,BODY,standard[ask],sensor_noise)
                p[ask]=probabilities(upd)
                used[k]+=ask
                final_counts[k]+=ask
            post[k]=p
            top=p.max(axis=1,keepdims=True)
            wins=np.isclose(p,top,rtol=1e-12,atol=1e-14)
            acc[k]+=wins[rows,true_ids]/wins.sum(axis=1)
            score[k]+=1-2*p[rows,true_ids]+(p*p).sum(axis=1)
    result={}
    for k in names:
        result[k]={"state_accuracy":float(acc[k].mean()/ticks),
                   "multiclass_brier_sum":float(score[k].mean()/ticks),
                   "mean_references":float(final_counts[k].mean())}
    # Intervals are over independently generated lifetimes, pairing policies within lifetime.
    delta=(acc["uncertainty"]-acc["periodic"])/ticks
    se=delta.std(ddof=1)/np.sqrt(n)
    result["uncertainty_minus_periodic_95_normal_paired"]=[float(delta.mean()+a*se) for a in [-1.96,1.96]]
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed",type=int,default=672901)
    parser.add_argument("--worlds",type=int,default=8192)
    parser.add_argument("--streams",type=int,default=2048)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    report={"seed":args.seed,"static_worlds":args.worlds,"stream_lifetimes":args.streams,
            "static":static_bench(args.worlds,args.seed),"stream":streaming_bench(args.streams,args.seed+1)}
    payload=json.dumps(report,indent=2)+"\n"
    if args.output:
        args.output.write_text(payload)
    else:
        print(payload,end="")


if __name__=="__main__":
    main()
