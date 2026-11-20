"""Stress the assumption that a learner is told the correct context. Analytically average over cue errors."""
import argparse,json
import numpy as np
from pathlib import Path
from association_test import Worlds,FAMILIES,K
def main():
    p=argparse.ArgumentParser();p.add_argument('output');args=p.parse_args();out={}
    for family in FAMILIES:
        worlds=Worlds(family,range(7500,7692));pred=np.zeros_like(worlds.targets)
        for s in range(192):
            for c in range(2):
                ids=worlds.ij[s,c]
                count=np.bincount(ids,minlength=K)
                np.divide(np.bincount(ids,weights=worlds.feedback[s,c],minlength=K),count,out=pred[s,c],where=count>0)
        out[family]={}
        for q in [0.,.1,.25,.5]:
            soft=(1-q)*pred+q*pred[:,::-1]
            metrics={}
            for name,h in [('hard',pred),('soft',soft)]:
                expected=(1-q)*(h-worlds.targets)**2+q*(h[:,::-1]-worlds.targets)**2
                metrics[name]=float(np.mean((expected*worlds.seen[:,None]).sum((1,2))/(2*worlds.seen.sum(1))))
            out[family][str(q)]=metrics
    print(json.dumps(out,indent=2));Path(args.output).write_text(json.dumps(out,indent=2))
if __name__=='__main__':main()
