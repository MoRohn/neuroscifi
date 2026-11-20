"""Exact Bayesian inference in a lightweight intervention task. No learned optimizer or neural network."""
import argparse,itertools,json
from pathlib import Path
import numpy as np
RULES=np.array([[0,0,0,1],[0,1,1,1],[0,0,1,1],[0,1,0,1]],float) # 00 01 10 11; AND, OR, A, B
EXTRA=np.array([[0,1,1,0],[1,0,0,1]],float)
ALL=np.array(list(itertools.product([0,1],repeat=4)),float)
N=4096
FLIP=.1
def run(world,prior,policy):
    rng=np.random.default_rng(944)
    truths=world[rng.permutation(np.arange(N)%len(world))]
    weights=np.ones((N,len(prior)))/len(prior)
    tie=rng.random((16,N,4));noise=rng.random((32,N))<FLIP
    random_action=rng.integers(0,4,(16,N));diag=3*rng.integers(0,2,(16,N))
    ix=np.arange(N)
    def update(a,t):
        observed=np.logical_xor(truths[ix,a],noise[t])
        agree=prior[:,a].T==observed[:,None]
        weights[:]*=np.where(agree,1-FLIP,FLIP)
        weights[:]/=weights.sum(1,keepdims=True)
    for t in range(16):update(np.full(N,3*(t%2)),t)
    result={}
    for b in range(17):
        prob=weights@prior
        if b in (0,2,4,8,16):
            # Exact expected correctness for fair random tie breaks.
            close=np.isclose(prob,.5,rtol=0,atol=1e-10)
            acc=np.where(close,.5,(prob>.5)==truths)
            noisy_pred=FLIP+(1-2*FLIP)*prob
            noisy_true=FLIP+(1-2*FLIP)*truths
            logloss=-np.mean(noisy_true*np.log(noisy_pred)+(1-noisy_true)*np.log(1-noisy_pred))
            result[b]={"all4_acc":float(acc.mean()),"off_diagonal_acc":float(acc[:,[1,2]].mean()),"expected_nll":float(logloss)}
        if b==16:break
        if policy=='observe':a=diag[b]
        elif policy=='random':a=random_action[b]
        else:
            uncertainty=prob*(1-prob) # ordering agrees with entropy at fixed symmetric observation noise
            a=np.argmax(uncertainty+1e-12*tie[b],axis=1)
        update(a,16+b)
    return result
def main():
    p=argparse.ArgumentParser();p.add_argument('output');args=p.parse_args()
    result={f:{k:{policy:run(world,prior,policy) for policy in ('observe','random','active')}
               for k,prior in [('4rules',RULES),('16rules',ALL)]} for f,world in [('in_family',RULES),('novel_rules',EXTRA)]}
    Path(args.output).write_text(json.dumps(result,indent=2))
    for f in result:
        for k,d in result[f].items():
            print(f,k,[(p,v[8]) for p,v in d.items()])
if __name__=='__main__':main()
