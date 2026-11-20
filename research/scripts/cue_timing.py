"""Descriptive cross-experiment comparison using Avraham et al.'s participant-level derived contrasts."""
from pathlib import Path
import argparse, json
import numpy as np
from scipy.io import loadmat
from scipy.stats import t, ttest_ind
def interval(x):
    mean = x.mean()
    error = t.ppf(.975,len(x)-1)*x.std(ddof=1)/np.sqrt(len(x))
    return [float(mean),float(mean-error),float(mean+error)]
def main():
    p=argparse.ArgumentParser();p.add_argument("directory");p.add_argument("output");a=p.parse_args()
    files = {"short":"Differential_Timing_Control.mat","long":"Differential_Timing_Delay.mat"}
    d={k:loadmat(Path(a.directory)/f,simplify_cells=True)['Differential'] for k,f in files.items()}
    out={}
    for phase in ("adapt","wash"):
        out[phase]={}
        for label,field in [("cue","da_sCurr"),("previous","da_sPrev")]:
            x,y=(np.asarray(d[k][phase][field]) for k in ("short","long"))
            assert x.shape==y.shape==(64,) and np.isfinite([x,y]).all()
            test=ttest_ind(x,y,equal_var=False)
            ci=test.confidence_interval()
            out[phase][label]={"short":interval(x),"long":interval(y),"short_minus_long":[x.mean()-y.mean(),ci.low,ci.high],"p":float(test.pvalue)}
    print(json.dumps(out,indent=2))
    Path(a.output).write_text(json.dumps(out,indent=2))
if __name__=="__main__":main()
