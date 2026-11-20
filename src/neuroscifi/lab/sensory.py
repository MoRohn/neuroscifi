"""Raw synthetic sensors -> learned prototypes -> causal state estimate."""
import argparse
import json
from pathlib import Path

import numpy as np

from .core import BODY, CAMERA, SPEAKER, balanced_ids, metrics, receive, update

RATE = 8000
NSAMPLES = 256
GRID = 24


def render(bits_v, bits_a, rng, condition):
    count = len(bits_v)
    row, col = np.ogrid[:GRID, :GRID]
    px = 6 + 11*bits_v + rng.normal(0,1.2,count)
    py = 11 + rng.normal(0,2,count)
    if condition == "rotated_and_transposed":
        px, py = GRID-1-py, px
    radius = rng.uniform(2.1,3.3,count)
    blob = np.exp(-((col-px[:,None,None])**2+(row-py[:,None,None])**2)/(2*radius[:,None,None]**2))
    bg = rng.uniform(.08,.27,count)[:,None,None]
    mono = bg + rng.uniform(.36,.65,count)[:,None,None]*blob + rng.normal(0,.075,blob.shape)
    rgb = np.stack([mono, .9*mono, .8*mono],axis=-1)
    rgb = np.uint8(255*np.clip(rgb,0,1))
    hz = 430+375*bits_a + rng.normal(0,18,count)
    if condition == "rotated_and_transposed":
        hz += 380
    if condition == "aliased_audio":
        hz = 600 + rng.normal(0,18,count)
    phase=rng.uniform(0,2*np.pi,count)[:,None]
    phase_seq=2*np.pi*hz[:,None]*np.arange(NSAMPLES)/RATE+phase
    sound=rng.uniform(.25,.65,count)[:,None]*np.sin(phase_seq)+rng.normal(0,.16,(count,NSAMPLES))
    return rgb,np.int16(np.clip(sound,-1,1)*32767)


def features(rgb, pcm):
    mono=rgb.astype(np.float32).mean(axis=3)/255
    central=mono-np.median(mono,axis=(1,2),keepdims=True)
    tiles=central.reshape(len(mono),4,6,4,6).mean(axis=(2,4)).reshape(len(mono),16)
    power=np.abs(np.fft.rfft((pcm.astype(np.float32)/32767)*np.hanning(NSAMPLES),axis=1))**2
    bands=power[:,4:52].reshape(len(mono),12,4).sum(axis=2)
    return tiles/(np.linalg.norm(tiles,axis=1,keepdims=True)+1e-7), bands/(bands.sum(axis=1,keepdims=True)+1e-7)


class Prototype:
    def __init__(self, x, y):
        self.centers=np.stack([x[y==k].mean(axis=0) for k in [0,1]])

    def decode(self, x):
        ds=((x[:,None]-self.centers[None,:])**2).sum(axis=2)
        return ds.argmin(axis=1),ds.min(axis=1)


def experiment(n, seed):
    rng=np.random.default_rng(seed)
    training=np.repeat([0,1],8)
    visual,audio=features(*render(training,training,rng,"nominal"))
    heads=[Prototype(f,training) for f in [visual,audio]]
    validation=np.repeat([0,1],96)
    calibration=features(*render(validation,validation,rng,"nominal"))
    reliability=[]
    thresholds=[]
    for head,f in zip(heads,calibration):
        pred,dist=head.decode(f)
        reliability.append(float(np.clip(((pred!=validation).sum()+1)/(len(validation)+2),.02,.49)))
        thresholds.append(float(np.quantile(dist,.97)))
    output={"prototype_pairs":len(training),"validation_pairs":len(validation),
            "error_floor":.02,"calibrated_decoder_errors":reliability}
    for condition in ["nominal","rotated_and_transposed"]:
        ids=balanced_ids(n,rng)
        accepted=np.zeros(2)
        correct=np.zeros(2)
        scores={k:np.zeros((n,8)) for k in ["always","distance_gate"]}
        for _ in range(8):
            actions=rng.integers(0,2,n)
            true_cam=CAMERA[ids]^actions
            true_speaker=SPEAKER[ids]^actions
            rgb,pcm=render(receive(true_cam,.08,rng),receive(true_speaker,.08,rng),rng,condition)
            for i,(head,f,channel,meaning) in enumerate(zip(heads,features(rgb,pcm),[CAMERA,SPEAKER],[true_cam,true_speaker])):
                pred,dist=head.decode(f)
                ok=dist<=thresholds[i]
                accepted[i]+=ok.sum()
                correct[i]+=(pred==meaning).sum()
                err=.08+reliability[i]-2*.08*reliability[i]
                update(scores["always"],channel,pred^actions,err)
                piece=scores["distance_gate"][ok].copy()
                update(piece,channel,(pred^actions)[ok],err)
                scores["distance_gate"][ok]=piece
        for _ in range(3):
            mechanical_reference=receive(BODY[ids],.10,rng)
            for current in scores.values():
                update(current,BODY,mechanical_reference,.10)
        output[condition]={"report_accuracy_visual_audio":(correct/(n*8)).tolist(),
                           "accepted_fraction_visual_audio":(accepted/(n*8)).tolist(),
                           **{k:metrics(s,ids) for k,s in scores.items()}}
    return output


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--worlds",type=int,default=1024)
    p.add_argument("--seed",type=int,default=93117)
    p.add_argument("--output",type=Path)
    a=p.parse_args()
    result=json.dumps(experiment(a.worlds,a.seed),indent=2)+"\n"
    if a.output:
        a.output.write_text(result)
    else:
        print(result,end="")
