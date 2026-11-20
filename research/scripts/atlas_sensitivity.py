"""Masking chemically related atlas channels together while holding out one anatomical functional network."""
import argparse,json
from pathlib import Path
import numpy as np
FAMILIES=[[0,1,2,3,4,5],[6,13,18],[7],[8,9,10],[11],[12],[14,17],[15],[16]]
def main():
    p=argparse.ArgumentParser();p.add_argument('directory');p.add_argument('output');a=p.parse_args();base=Path(a.directory)
    X=np.loadtxt(base/'receptors.csv',delimiter=',');labels=np.loadtxt(base/'labels.txt',dtype=str,usecols=[1])
    groups=np.array([s.split('_')[2] for s in labels]);assert sorted(sum(FAMILIES,[]))==list(range(X.shape[1]))
    result={}
    for penalty in [.001,.01,.1,1.]:
        residual=np.zeros(6);den=0.
        for g in np.unique(groups):
            tr=X[groups!=g];te=X[groups==g];mu=tr.mean(0);sd=tr.std(0,ddof=1);tr=(tr-mu)/sd;te=(te-mu)/sd
            _,_,V=np.linalg.svd(tr,full_matrices=False)
            den+=np.square(te).sum()
            for cols in FAMILIES:
                obs=np.setdiff1d(range(19),cols);xt=te[:,obs]
                for j,k in enumerate([1,4,7,12,16]):
                    B=V[:k].T;O=B[obs];scores=np.linalg.solve(O.T@O+penalty*np.eye(k),O.T@xt.T)
                    pred=(B[cols]@scores).T;residual[j]+=np.square(te[:,cols]-pred).sum()
                Ot=tr[:,obs]
                beta=np.linalg.solve(Ot.T@Ot+(len(tr)-1)*penalty*np.eye(len(obs)),Ot.T@tr[:,cols])
                residual[-1]+=np.square(te[:,cols]-xt@beta).sum()
        result[str(penalty)]=dict(zip(['1','4','7','12','16','ridge'],(1-residual/den).tolist()))
    print(json.dumps(result,indent=2));Path(a.output).write_text(json.dumps(result,indent=2))
if __name__=='__main__':main()
