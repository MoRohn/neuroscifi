"""Spatial transfer of compressed receptor maps. Run with the published scale100 matrix and corresponding seven-network label file."""
import argparse
import json
from pathlib import Path
import numpy as np

RECEPTORS = ["5HT1a","5HT1b","5HT2a","5HT4","5HT6","5HTT","A4B2","CB1","D1","D2","DAT","GABAa","H3","M1","mGluR5","MOR","NET","NMDA","VAChT"]
RANKS = (1, 4, 7, 12)

def cross_validate(x, groups):
    result = {}
    for rank in RANKS:
        sums = np.zeros(4)
        detail = {}
        for group in np.unique(groups):
            train, test = x[groups != group], x[groups == group]
            mean, scale = train.mean(0), train.std(0, ddof=1)
            train, test = (train-mean)/scale, (test-mean)/scale
            _, _, vt = np.linalg.svd(train, full_matrices=False)
            basis = vt[:rank].T
            residual = test - test@basis@basis.T
            missing = np.empty_like(test)
            for j in range(x.shape[1]):
                idx = np.arange(x.shape[1]) != j
                observed = basis[idx]
                # Fixed penalty on latent scores, declared before inspecting scores.
                scores = np.linalg.solve(observed.T@observed + .01*np.eye(rank), observed.T@test[:,idx].T)
                missing[:,j] = test[:,j] - basis[j]@scores
            totals = np.array([np.square(test).sum(), np.square(residual).sum(), np.square(missing).sum(), test.size])
            sums += totals
            detail[str(group)] = [len(test), 1-totals[1]/totals[0], 1-totals[2]/totals[0]]
        result[rank] = {"observed_reconstruction": 1-sums[1]/sums[0], "missing_channel": 1-sums[2]/sums[0], "folds": detail}
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument("matrix");p.add_argument("labels");p.add_argument("output")
    args=p.parse_args()
    x = np.loadtxt(args.matrix,delimiter=",")
    labels = np.loadtxt(args.labels, dtype=str, usecols=[1])
    assert x.shape == (100,19) and len(labels)==len(x) and np.isfinite(x).all()
    hemi = np.array([label.split("_")[1] for label in labels])
    networks = np.array([label.split("_")[2] for label in labels])
    assert len(np.unique(networks))==7 and list(np.unique(hemi, return_counts=True)[1])==[50,50]
    ev = np.linalg.eigvalsh(np.corrcoef(x.T))[::-1]
    out = {"in_sample":{i: ev[:i].sum()/ev.sum() for i in RANKS},"hemisphere":cross_validate(x,hemi),"network":cross_validate(x,networks)}
    Path(args.output).write_text(json.dumps(out,indent=2))
    for k in RANKS:
        print(k,out["in_sample"][k],out["hemisphere"][k]["observed_reconstruction"],out["hemisphere"][k]["missing_channel"],out["network"][k]["observed_reconstruction"],out["network"][k]["missing_channel"])
    for k,v in out["network"][4]["folds"].items(): print(k,v)
if __name__=="__main__":main()
