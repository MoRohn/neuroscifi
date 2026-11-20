"""Predict the next measured reaching angle. This task supplies experimenter-coded cue identity."""
import argparse,json
from pathlib import Path
import numpy as np
from scipy.io import loadmat
FILES={"short":"data_diffCond_Timing_LongITI_Comb.mat","long":"data_diffCond_Timing_VaryingDelay_Comb.mat"}
def designs(angles,cues):
    designs=[];labels=[]
    for observations,context in zip(angles,cues):
        fast=slow=last=0.;prev=0.;seen=False
        rows=[];out=[]
        for t,(angle,cue) in enumerate(zip(observations,context)):
            probe=t>=600
            # Score after a first valid observation. All continuous predictors use t-1 or earlier.
            prior_feedback=prev if 1<=t<=600 else 0.
            if seen and np.isfinite(angle):
                centered=cue-.5
                rows.append([1.,int(probe),last,fast,slow,
                             np.exp(-t/30),np.exp(-(t-600)/30) if probe else 0.,
                             prior_feedback,centered*int(not probe),centered*int(probe)])
                out.append(angle)
            if np.isfinite(angle):
                if not seen:fast=slow=angle
                else:
                    fast += .2*(angle-fast)
                    slow += .025*(angle-slow)
                last=angle;seen=True
            prev=(cue-.5)
        designs.append(np.array(rows));labels.append(np.array(out))
    return designs,labels

def run_group(data):
    xx,yy=designs(data['ha'],data['cs_p'])
    rng=np.random.default_rng(735)
    split=np.array_split(rng.permutation(len(xx)),8)
    scores=np.zeros((len(xx),3,2))
    for test in split:
        train=np.setdiff1d(np.arange(len(xx)),test)
        x=np.concatenate([xx[i]/np.sqrt(len(yy[i])) for i in train])
        y=np.concatenate([yy[i]/np.sqrt(len(yy[i])) for i in train])
        for j,count in enumerate([7,8,10]):
            b=np.linalg.lstsq(x[:,:count],y,rcond=None)[0]
            for i in test:
                pred=xx[i][:,:count]@b
                scores[i,j,:]=[np.square(pred-yy[i]).mean(),np.abs(pred-yy[i]).mean()]
    # Participant is the unit of aggregation. Bootstrap intervals condition on CV predictions.
    boot=np.random.default_rng(248).integers(0,len(xx),(10000,len(xx)))
    improvement=scores[:,1,0]-scores[:,2,0]
    rel=(scores[boot,1,0].mean(1)-scores[boot,2,0].mean(1))/scores[boot,1,0].mean(1)*100
    return {"participants":len(xx),"predictions":sum(map(len,yy)),
            "rmse":np.sqrt(scores[:,:,0].mean(0)).tolist(),"mae":scores[:,:,1].mean(0).tolist(),
            "relative_mse_gain_cue":100*improvement.mean()/scores[:,1,0].mean(),
            "gain_conditional_bootstrap_95":np.quantile(rel,[.025,.975]).tolist(),
            "subjects_improved":int((improvement>0).sum())}
def main():
    p=argparse.ArgumentParser();p.add_argument('directory');p.add_argument('output');a=p.parse_args()
    out={}
    for k,v in FILES.items():
        d=loadmat(Path(a.directory)/v,simplify_cells=True)['diff_cond']
        assert d['ha'].shape==d['cs_p'].shape==(64,800)
        assert set(np.unique(d['cs_p']))=={0,1}
        out[k]=run_group(d)
    print(json.dumps(out,indent=2));Path(a.output).write_text(json.dumps(out,indent=2))
if __name__=='__main__':main()
