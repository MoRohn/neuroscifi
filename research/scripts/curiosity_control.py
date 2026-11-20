"""Test uncertainty-seeking vs information gain with an irreducibly noisy observation available."""
import argparse,json
from pathlib import Path
import numpy as np
from intervention_test import RULES,FLIP,N
def H(p):
 p=np.clip(p,1e-12,1-1e-12);return -(p*np.log(p)+(1-p)*np.log(1-p))
def one(policy):
 rng=np.random.default_rng(741)
 world=rng.permutation(np.arange(N)%4)
 likelihood=np.c_[FLIP+(1-2*FLIP)*RULES,np.ones(4)*.5]
 posterior=np.full((N,4),.25)
 # A 00 or 11 observation has the same likelihood under all four hypotheses; their posterior stays uniform.
 noise=rng.random((8,N));ties=rng.random((8,N,5));counts=np.zeros(N)
 for t in range(8):
    prediction=posterior@likelihood
    if policy=='random':actions=ties[t].argmax(1)
    else:
        utility=H(prediction)
        if policy=='information':utility-=posterior@H(likelihood)
        actions=(utility+1e-12*ties[t]).argmax(1)
    outcome=noise[t]<likelihood[world,actions]
    p=likelihood[:,actions].T
    posterior*=np.where(outcome[:,None],p,1-p)
    posterior/=posterior.sum(1,keepdims=True)
    counts+=(actions==4)
 p=posterior@RULES[:,[1,2]];truth=RULES[world][:,[1,2]]
 correct=np.where(np.isclose(p,.5),.5,(p>.5)==truth)
 return {'mean_noise_actions':counts.mean(),'diagnostic_accuracy':correct.mean()}
def main():
 p=argparse.ArgumentParser();p.add_argument('output');a=p.parse_args()
 d={m:one(m) for m in ['random','entropy','information']}
 print(d);Path(a.output).write_text(json.dumps(d,indent=2))
if __name__=='__main__':main()
