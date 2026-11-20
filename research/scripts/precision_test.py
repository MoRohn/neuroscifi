"""Quantized online plasticity probe. Output quantization only. Noise and targets are shared across methods."""
import argparse,json
from pathlib import Path
import numpy as np
N=256
L=2048
METHODS=('fp32','fp16','int8_nearest','int8_stochastic','int8_residual')
def run(alpha):
    rng=np.random.default_rng(440);round_rng=np.random.default_rng(441)
    p=rng.uniform(.15,.60,N);q=rng.uniform(-.60,-.15,N)
    weights={m:np.zeros(N,dtype=np.float32) for m in METHODS}
    resid=np.zeros(N,dtype=np.float16);mse={m:[] for m in METHODS};writes={m:0 for m in METHODS}
    for target in (p,q,p):
        period={m:[] for m in METHODS}
        for t in range(L):
            observation=target+rng.normal(0,.05,N)
            for m in METHODS:
                w=weights[m]
                if t>=L-256:period[m].append(np.mean((w-target)**2))
                delta=alpha*(observation-w)
                if m=='fp32':nxt=(w+delta).astype(np.float32)
                elif m=='fp16':nxt=(w+delta).astype(np.float16).astype(np.float32)
                else:
                    proposal=w+delta+(resid if m=='int8_residual' else 0)
                    scaled=np.clip(proposal,-1,1)*127
                    if m=='int8_stochastic':
                        floor=np.floor(scaled)
                        code=floor+(round_rng.random(N)<(scaled-floor))
                    else:code=np.rint(scaled)
                    nxt=(code/127).astype(np.float32)
                    if m=='int8_residual':resid=(proposal-nxt).astype(np.float16)
                writes[m]+=int(np.count_nonzero(nxt!=w));weights[m]=nxt
        for m in METHODS:mse[m].append(float(np.sqrt(np.mean(period[m]))))
    return {m:{'late_rmse':mse[m],'visible_weight_change_fraction':writes[m]/(N*L*3)} for m in METHODS}
def main():
    p=argparse.ArgumentParser();p.add_argument('output');args=p.parse_args()
    out={a:run(a) for a in [.001,.005,.02]}
    for a,d in out.items():print(a,[(m,round(np.mean(v['late_rmse']),4)) for m,v in d.items()])
    Path(args.output).write_text(json.dumps(out,indent=2))
if __name__=='__main__':main()
