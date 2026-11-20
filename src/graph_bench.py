"""Online sparse directed graph learning in artificial, fully observed linear worlds."""
import argparse
import json
from pathlib import Path
import numpy as np

N=24
K=3
WINDOW=32
STEPS=1536
SCALE=.75/127


def layout(rng):
    m=np.zeros((N,N),bool)
    for j in range(N):
        m[j,rng.choice(np.delete(np.arange(N),j),K,replace=False)]=True
    return m


def world(rng):
    m=layout(rng)
    a=np.zeros((N,N))
    a[m]=rng.choice([-1.,1.],size=m.sum())*rng.uniform(.17,.25,m.sum())
    b=a.copy()
    for j in range(N):
        old=rng.choice(np.where(m[j])[0])
        pool=np.where(~m[j] & (np.arange(N)!=j))[0]
        new=rng.choice(pool)
        b[j,old]=0
        b[j,new]=rng.choice([-1.,1.])*rng.uniform(.17,.25)
    return a,b


def stream(A,length,rng):
    prev=np.zeros(N)
    states=np.empty((length,N))
    targets=np.empty_like(states)
    for t in range(length):
        residual=A@prev+rng.normal(0,.065,N)
        states[t]=prev
        targets[t]=residual
        # These independent excitations give each source a chance to vary.
        # The fixed self path and measured external drive are removed from the target.
        drive=rng.normal(0,.6,N)*(rng.random(N)<.22)
        prev=.2*prev+residual+drive
    return states,targets


def mse(a,xy):
    x,y=xy
    return float(np.mean((x@a.T-y)**2))


def simulate(seed, observe_epoch=None):
    rng=np.random.default_rng(seed)
    a,b=world(rng)
    frozen=layout(rng)
    use=frozen.copy()
    dense=np.zeros((N,N))
    fixed=dense.copy()
    growing=dense.copy()
    score=dense.copy()
    w1=None
    out={}
    for era,A in enumerate([a,b]):
        x,y=stream(A,STEPS,rng)
        for left in range(0,STEPS,WINDOW):
            xx=x[left:left+WINDOW]
            yy=y[left:left+WINDOW]
            denom=(xx*xx).sum(axis=0)+.3
            for mat,mask in [(dense,None),(fixed,frozen),(growing,use)]:
                residual=yy-xx@mat.T
                grad=(residual.T@xx)/denom
                np.fill_diagonal(grad,0)
                mat += .28*grad if mask is None else .28*grad*mask
                if mat is growing:
                    score=.85*score+.15*grad
            if (left//WINDOW+1)%4==0:
                for j in range(N):
                    inside=np.where(use[j])[0]
                    outside=np.where((~use[j]) & (np.arange(N)!=j))[0]
                    drop=inside[np.argmin(abs(growing[j,inside]))]
                    add=outside[np.argmax(abs(score[j,outside]))]
                    if abs(score[j,add])>1.15*abs(growing[j,drop])+.035:
                        growing[j,drop]=0
                        use[j,drop]=False
                        use[j,add]=True
                        growing[j,add]=0
        if observe_epoch is not None:
            observe_epoch(era, A.copy(), dense.copy(), growing.copy())
        test=stream(A,512,rng)
        quant=np.rint(growing/SCALE).clip(-127,127).astype(np.int8)
        ans={"dense_mse":mse(dense,test),"fixed_mse":mse(fixed,test),"evolving_mse":mse(growing,test),
             "int8_readout_mse":mse(quant.astype(float)*SCALE,test),
             "learned_edge_precision":float((use & (A!=0)).sum()/use.sum()),
             "fixed_edge_precision":float((frozen & (A!=0)).sum()/frozen.sum())}
        # Preserve each receiver's weight histogram and degree; scramble its source identities.
        perm_rng=np.random.default_rng(seed*11+era)
        shuffled=np.zeros_like(growing)
        for j in range(N):
            pool=np.delete(np.arange(N),j)
            shuffled[j,pool]=perm_rng.permutation(growing[j,pool])
        ans["rewired_control_mse"]=mse(shuffled,test)
        if w1 is None:
            w1=growing.copy()
            m1=use.copy()
        else:
            oldtest=stream(a,512,rng)
            ans["old_world_mse_after_change"]=mse(growing,oldtest)
            ans["old_world_mse_epoch_snapshot"]=mse(w1,oldtest)
            ans["fraction_epoch_one_edges_replaced"]=float((m1 & ~use).sum()/m1.sum())
        out[str(era+1)]=ans
    return out


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--worlds",type=int,default=96)
    p.add_argument("--seed",type=int,default=238194)
    p.add_argument("--output",type=Path)
    a=p.parse_args()
    worlds=[simulate(a.seed+i) for i in range(a.worlds)]
    means={era:{k:float(np.mean([w[era][k] for w in worlds])) for k in worlds[0][era]} for era in ["1","2"]}
    body=json.dumps({"worlds":a.worlds,"seed":a.seed,"means":means,"per_world":worlds},indent=2)+"\n"
    if a.output:
        a.output.write_text(body)
    else:
        print(body,end="")
