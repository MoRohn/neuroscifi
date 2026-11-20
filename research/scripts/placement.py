"""Balanced graph placement on the group Schaefer100 structural and functional matrices."""
import argparse,json
from pathlib import Path
import numpy as np
def cut(W, side):
    return float(W[np.ix_(side,~side)].sum()/(W.sum()/2))
def refine(W,s):
    s=s.copy(); n=len(s)
    while True:
        p,q=np.where(s)[0],np.where(~s)[0]
        # gain in cut reduction for toggling one node, then correct for their shared edge
        cross=np.where(s,W[:,~s].sum(1),W[:,s].sum(1))
        gain=2*cross-W.sum(1)
        pair=gain[p,None]+gain[None,q]-2*W[np.ix_(p,q)]
        i,j=np.unravel_index(pair.argmax(),pair.shape)
        if pair[i,j]<=1e-12:break
        s[p[i]]=False;s[q[j]]=True
    assert s.sum()==n//2
    return s
def fit(W,hemi):
    W=(W+W.T)/2;L=np.diag(W.sum(1))-W
    _, vec=np.linalg.eigh(L)
    n=len(W); s=np.zeros(n,dtype=bool);s[np.argsort(vec[:,1])[:n//2]]=True
    rng=np.random.default_rng(510)
    starts=[hemi,s]+[rng.permutation(hemi) for _ in range(24)]
    sols=[refine(W,z) for z in starts]
    return min(sols,key=lambda z:cut(W,z))
def main():
    p=argparse.ArgumentParser();p.add_argument('directory');p.add_argument('output');a=p.parse_args();path=Path(a.directory)
    w=np.load(path/'sc_weighted.npy');binary=np.load(path/'sc_binary.npy');f=np.maximum(np.load(path/'fc_weighted.npy'),0)
    for m in (w,binary,f):np.fill_diagonal(m,0)
    assert np.allclose(w,w.T) and np.allclose(f,f.T) and np.array_equal(w>0,binary)
    labels=np.loadtxt(path/'labels.txt',dtype=str,usecols=[1]);hemi=np.array([s.split('_')[1]=='LH' for s in labels])
    def metrics(s):
        return dict(structural_weight=cut(w,s),structural_edges=cut(binary,s),functional_weight=cut(f,s),degree_imbalance=abs(w[s].sum()-w[~s].sum())/w.sum())
    out={'hemisphere':metrics(hemi)}
    rng=np.random.default_rng(679);zs=[metrics(rng.permutation(hemi)) for _ in range(2000)]
    out['random']={k:float(np.mean([z[k] for z in zs])) for k in zs[0]}
    for alpha in [0.,.25,.5,.75,1.]:
        W=(1-alpha)*w/w.sum()+alpha*f/f.sum()
        s=fit(W,hemi)
        out[str(alpha)]=metrics(s)
    print(json.dumps(out,indent=2));Path(a.output).write_text(json.dumps(out,indent=2))
if __name__=='__main__':main()
