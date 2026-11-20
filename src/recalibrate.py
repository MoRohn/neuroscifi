"""Evaluate a source-specific calibration contract on fresh held-back rooms."""
import argparse
import json
from pathlib import Path
import numpy as np
from core import BODY, CAMERA, SPEAKER, balanced_ids, metrics, receive, update
from sensory import Prototype, features, render

CONDITIONS=("nominal","rotated_and_transposed","aliased_audio")


def capture_known(domain,rng,per_class):
    labels=np.tile([0,1],per_class)
    return labels,features(*render(labels,labels,rng,domain))


def fit(domain,rng,per_class):
    y,x=capture_known(domain,rng,per_class)
    return [Prototype(sensor,y) for sensor in x]


def validate(heads,domain,rng,per_class):
    y,x=capture_known(domain,rng,per_class)
    errors=[]
    hits=[]
    for head,sensor in zip(heads,x):
        pred,_=head.decode(sensor)
        ok=(pred==y)
        counts=[int(ok[y==k].sum()) for k in (0,1)]
        usable=(ok.mean()>=13/16 and min(counts)/per_class>=6/8)
        errors.append(min(.5,1-(ok.sum()+1)/(len(y)+2)) if usable else .5)
        hits.append(counts)
    return errors,hits


def trial(seed,domain,n):
    rng=np.random.default_rng(seed)
    # Both policies receive exactly the same 20 keyed pairs at an announced hardware boundary.
    born=fit("nominal",rng,8)
    original_errors,_=validate(born,"nominal",rng,96)
    refreshed=fit(domain,rng,2)
    checked,hits=validate(refreshed,domain,rng,8)
    models={"frozen":(born,original_errors),
            "refit_unchecked":(refreshed,original_errors),
            "recalibrated":(refreshed,checked)}
    scores={key:np.zeros((n,8)) for key in models}
    truth=balanced_ids(n,rng)
    for _ in range(8):
        act=rng.integers(0,2,n)
        c=receive(CAMERA[truth]^act,.08,rng)
        t=receive(SPEAKER[truth]^act,.08,rng)
        feats=features(*render(c,t,rng,domain))
        for key,(heads,errors) in models.items():
            for head,err,x,channel in zip(heads,errors,feats,[CAMERA,SPEAKER]):
                pred,_=head.decode(x)
                effective=.08+err-2*.08*err
                update(scores[key],channel,pred^act,effective)
    for _ in range(3):
        anchor=receive(BODY[truth],.10,rng)
        for current in scores.values():
            update(current,BODY,anchor,.10)
    return {"usable":[bool(e<.5) for e in checked],"hits":hits,
            **{k:metrics(s,truth) for k,s in scores.items()}}


def suite(rooms=24,n=128,seed=2603194):
    all_results={}
    for i,domain in enumerate(CONDITIONS):
        records=[trial(seed+i*10000+j,domain,n) for j in range(rooms)]
        delta=np.array([r["recalibrated"]["exact_state_accuracy_ties_split"]-r["frozen"]["exact_state_accuracy_ties_split"] for r in records])
        avg=float(delta.mean())
        se=float(delta.std(ddof=1)/np.sqrt(rooms))
        all_results[domain]={
            "usable_rates":np.mean([r["usable"] for r in records],axis=0).tolist(),
            "accuracy_delta_normal_95_by_room":[avg-1.96*se,avg+1.96*se],
            **{k:{m:float(np.mean([r[k][m] for r in records])) for m in records[0][k]} for k in ["recalibrated","refit_unchecked","frozen"]},
            "room_results":records}
    return {"rooms_per_condition":rooms,"lifetimes_per_room":n,"seed":seed,"conditions":all_results}


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seed",type=int,default=2603194)
    p.add_argument("--rooms",type=int,default=24)
    p.add_argument("--worlds",type=int,default=128)
    p.add_argument("--output",type=Path)
    a=p.parse_args()
    payload=json.dumps(suite(a.rooms,a.worlds,a.seed),indent=2)+"\n"
    if a.output:
        a.output.write_text(payload)
    else:
        print(payload,end="")
