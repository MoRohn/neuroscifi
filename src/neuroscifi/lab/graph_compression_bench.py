"""Validation-selected graph packing, independently scored on unseen trajectories."""
import argparse
from collections import Counter
import json
from pathlib import Path
import numpy as np
from .graph_bench import simulate,stream,mse
from ..graph_store import encode,decode


def run(worlds=64):
    scores=[]
    chosen=Counter()
    for seed in range(813240,813240+worlds):
        def evaluate(epoch, world, dense, growing):
            if epoch!=1:
                return
            val=stream(world,256,np.random.default_rng(seed+100000))
            test=stream(world,512,np.random.default_rng(seed+200000))
            dense=dense.astype("f4")
            baseline=mse(dense,val)
            options=[]
            for degree,bits in [(dense.shape[1],32)]+[(k,b) for k in (2,3,4,6,12) for b in (4,8)]:
                data=encode(dense,degree,bits)
                estimate=decode(data)
                if mse(estimate,val)<=1.02*baseline:
                    options.append((len(data),bits,degree,estimate))
            length,bits,degree,estimate=min(options,key=lambda row:row[0])
            chosen[f"{bits}bit_k{degree}"]+=1
            scores.append([length,mse(estimate,test),mse(dense,test),dense.nbytes,
                           mse(estimate,test)>1.02*mse(dense,test)])
        simulate(seed,observe_epoch=evaluate)
    size,loss,base,size_base,miss=np.mean(scores,axis=0)
    return {"worlds":worlds,"seed_start":813240,"selected":dict(chosen),"raw_dense_fp32_bytes":int(size_base),
            "mean_complete_graph_bytes":float(size),"aggregate_ratio":float(size_base/size),
            "mean_test_mse_selected":float(loss),"mean_test_mse_dense":float(base),
            "heldout_fraction_over_validation_tolerance":float(miss),"selection_tolerance":.02}


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",type=Path)
    p.add_argument("--worlds",type=int,default=64)
    a=p.parse_args()
    s=json.dumps(run(a.worlds),indent=2)+"\n"
    if a.output: a.output.write_text(s)
    else: print(s,end="")
