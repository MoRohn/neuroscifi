"""Independent reading noise and persistent fixture faults have different likelihoods."""
import argparse
import json
from pathlib import Path
import numpy as np
from .core import BODY, CAMERA, SPEAKER, balanced_ids, metrics, probabilities, receive, update


def group_log_likelihood(readings, flip_prob, reading_error):
    """Integrate the shared binary polarity of ONE fixture out of its likelihood."""
    ones=readings.sum(axis=0)[:,None]
    n=len(readings)
    matches=np.where(BODY[None,:]==1,ones,n-ones)
    good=matches*np.log1p(-reading_error)+(n-matches)*np.log(reading_error)
    inverted=matches*np.log(reading_error)+(n-matches)*np.log1p(-reading_error)
    if flip_prob==0:
        return good
    return np.logaddexp(np.log1p(-flip_prob)+good,np.log(flip_prob)+inverted)


def record(scores,truth):
    p=probabilities(scores)
    high=p.max(axis=1)>.95
    wrong=p.argmax(axis=1)!=truth
    return {**metrics(scores,truth),
            "over_95_coverage":float(high.mean()),
            "wrong_and_over_95_fraction_all_worlds":float((high & wrong).mean())}


def experiment(n=16384,seed=198675):
    rng=np.random.default_rng(seed)
    ids=balanced_ids(n,rng)
    initial=np.zeros((n,8))
    for _ in range(12):
        for signal in [CAMERA,SPEAKER]:
            update(initial,signal,receive(signal[ids],.10,rng),.10)
    readings_noise=rng.random((9,n))<.10
    local_faults=rng.random((3,n))<.15
    data={}
    for name,count,common in [("one_fixture",1,True),("three_independent",3,False),("three_shared_root",3,True)]:
        ids_by_reading=np.arange(9)%count
        faults=local_faults[0] if common else local_faults[ids_by_reading]
        observations=BODY[ids][None,:] ^ faults ^ readings_noise
        answers={}
        for policy,pfault,root_count in [("independent_votes",0,count),("by_fixture",.15,count),
                                         ("by_root",.15,1 if common else count),
                                         ("root_prior_low",.03,1 if common else count),
                                         ("root_prior_high",.30,1 if common else count)]:
            scores=initial.copy()
            group_ids=np.arange(9)%root_count
            for group in range(root_count):
                scores+=group_log_likelihood(observations[group_ids==group],pfault,.10)
            answers[policy]=record(scores,ids)
        data[name]=answers
    return {"worlds":n,"seed":seed,"fixture_flip_probability":.15,"reading_error":.10,"conditions":data}


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--worlds",type=int,default=16384)
    p.add_argument("--seed",type=int,default=198675)
    p.add_argument("--output",type=Path)
    a=p.parse_args()
    payload=json.dumps(experiment(a.worlds,a.seed),indent=2)+"\n"
    if a.output:
        a.output.write_text(payload)
    else:
        print(payload,end="")
