"""Synthetic audiovisual association test with executable controls. Each simulation seed is a separate world."""
import argparse,json
from pathlib import Path
from itertools import product
import numpy as np
D=12
K=D*D
T=384
FAMILIES=('shared','reversed','arbitrary')
RATES=(.015625,.0625,.25,.5)
class Worlds:
    def __init__(self,family,seeds):
        self.idx=np.arange(len(seeds));self.targets=[];self.seen=[];self.ij=[];self.feedback=[]
        for seed in seeds:
            rng=np.random.default_rng(seed)
            a=rng.normal(size=D);b=rng.normal(size=D)
            base=(a[:,None]+b[None,:]).reshape(-1)/np.sqrt(2)
            sign=1 if family=='shared' else -1
            base=np.stack([base,sign*base])
            truth=rng.normal(size=(2,K)) if family=='arbitrary' else (base+.5*rng.normal(size=(2,K)))/np.sqrt(1.25)
            eligible=np.sort(rng.choice(K,size=108,replace=False))
            mask=np.isin(np.arange(K),eligible)
            # Every eligible pair appears once, remaining trials randomly repeat. Shuffle the block.
            seq=np.stack([rng.permutation(np.r_[eligible,rng.choice(eligible,T-len(eligible))]) for _ in range(2)])
            y=truth[np.arange(2)[:,None],seq]+rng.normal(scale=.2,size=(2,T))
            self.targets.append(truth);self.seen.append(mask);self.ij.append(seq);self.feedback.append(y)
        self.targets=np.array(self.targets);self.seen=np.array(self.seen);self.ij=np.array(self.ij);self.feedback=np.array(self.feedback)
    def run(self,kind,eta=0.,rho=0.):
        n=len(self.idx);ix=self.idx
        wa=np.zeros((n,D));wb=np.zeros((n,D));pair=np.zeros((n,2,K));count=np.zeros((n,2,K))
        def errors(c):
            base=(wa[:,:,None]+wb[:,None,:]).reshape(n,K)
            if kind in ('table','backoff','adaptive'):
                yhat=pair[:,c].copy()
                if kind in ('backoff','adaptive'):
                    counts=count[:,c].reshape(n,D,D)
                    sums=(pair[:,c]*count[:,c]).reshape(n,D,D)
                    row=sums.sum(2)/np.maximum(counts.sum(2),1)
                    col=sums.sum(1)/np.maximum(counts.sum(1),1)
                    grand=sums.sum((1,2))/np.maximum(counts.sum((1,2)),1)
                    strength=eta
                    if kind=='adaptive':
                        # Each known key becomes a pseudo-unseen key. All its repeats are excluded.
                        def excl(total_sum,total_count):
                            return (total_sum-sums)/np.maximum(total_count-counts,1)
                        gr=excl(sums.sum((1,2))[:,None,None],counts.sum((1,2))[:,None,None])
                        r=excl(sums.sum(2,keepdims=True),counts.sum(2,keepdims=True))
                        cmean=excl(sums.sum(1,keepdims=True),counts.sum(1,keepdims=True))
                        feature=r+cmean-2*gr
                        y=pair[:,c].reshape(n,D,D)-gr
                        eligible=(counts>0)&(counts.sum(2,keepdims=True)>counts)&(counts.sum(1,keepdims=True)>counts)
                        slope=(feature*y*eligible).sum((1,2))/np.maximum((feature**2*eligible).sum((1,2)),1e-8)
                        strength=np.clip(slope,0,1)[:,None,None]
                    estimate=grand[:,None,None]+strength*(row[:,:,None]+col[:,None,:]-2*grand[:,None,None])
                    yhat=np.where(count[:,c]>0,yhat,estimate.reshape(n,K))
            else:
                yhat=base+pair[:,c]
            sq=(yhat-self.targets[:,c])**2
            return [(sq*m).sum(1)/m.sum(1) for m in [self.seen,~self.seen]]
        for c in range(2):
            for t in range(T):
                keys=self.ij[:,c,t];a=keys//D;b=keys%D;y=self.feedback[:,c,t]
                if kind in ('table','backoff','adaptive'):
                    count[ix,c,keys]+=1
                    pair[ix,c,keys]+=(y-pair[ix,c,keys])/count[ix,c,keys]
                else:
                    base=wa[ix,a]+wb[ix,b];res=y-base-pair[ix,c,keys]
                    shared_target=y-base if kind=='local' else res
                    wa[ix,a]+=eta*shared_target/2
                    wb[ix,b]+=eta*shared_target/2
                    pair[ix,c,keys]+=rho*res
            if c==0: before=errors(0)[0]
        a_seen,a_new=errors(0);b_seen,b_new=errors(1)
        return np.stack([a_seen,b_seen,(a_new+b_new)/2,a_seen-before],axis=1)
def params(kind):
    if kind in ('table','adaptive'):return [(0.,0.)]
    if kind=='backoff':return [(x,0.) for x in [0.,.25,.5,.75,1.]]
    if kind=='tied':return [(r,r) for r in RATES]
    return list(product(RATES,RATES))
def main():
    p=argparse.ArgumentParser();p.add_argument('output');p.add_argument('--test-start',type=int,default=5000);args=p.parse_args()
    train=[Worlds(f,range(40)) for f in FAMILIES]
    test=[Worlds(f,range(args.test_start,args.test_start+192)) for f in FAMILIES]
    result={};score_arrays={}
    for kind in ['table','backoff','adaptive','tied','split','local']:
        candidates=params(kind)
        valid=[np.mean([w.run(kind,*rates)[:,:3].mean() for w in train]) for rates in candidates]
        rates=candidates[int(np.argmin(valid))]
        print(kind,rates,'validation',min(valid),flush=True)
        score_arrays[kind]=[w.run(kind,*rates) for w in test]
        result[kind]={'rates':rates,'validation_loss':float(min(valid)),
                      'families':{f:arr.mean(0).tolist() for f,arr in zip(FAMILIES,score_arrays[kind])}}
    # Paired differences across worlds; the mean of each world's three primary endpoints is the selection metric.
    boot=np.random.default_rng(901).integers(0,192,(10000,192))
    cmp={}
    for j,f in enumerate(FAMILIES):
        z=(score_arrays['local'][j][:,:3]-score_arrays['split'][j][:,:3]).mean(1)
        cmp[f]=[z.mean(),*np.quantile(z[boot].mean(1),[.025,.975])]
    result['local_minus_split']=cmp
    result['adaptive_minus_backoff']={}
    for j,f in enumerate(FAMILIES):
        z=score_arrays['adaptive'][j][:,2]-score_arrays['backoff'][j][:,2]
        result['adaptive_minus_backoff'][f]=[z.mean(),*np.quantile(z[boot].mean(1),[.025,.975])]
    result['matched_rates']={}
    for label in ['split','local']:
        rates=result[label]['rates']
        result['matched_rates'][label]={}
        for j,w in enumerate(test):
            diff=(w.run('local',*rates)[:,:3]-w.run('split',*rates)[:,:3]).mean(1)
            result['matched_rates'][label][FAMILIES[j]]=[diff.mean(),*np.quantile(diff[boot].mean(1),[.025,.975])]
    print(json.dumps(result,indent=1));Path(args.output).write_text(json.dumps(result,indent=2))
if __name__=='__main__':main()
