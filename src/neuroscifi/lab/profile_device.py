"""Portable CPU / optional CUDA device benchmark for a recurrent rollout."""
import argparse
import json
import platform
import sys
import time
from pathlib import Path
import numpy as np


def rollout(xp,x,weights,bias,steps):
    for _ in range(steps):
        x=xp.tanh(x @ weights + bias)
    return x


def clock(work,sync,repeats):
    for _ in range(5):
        work()
    sync()
    samples=[]
    for _ in range(repeats):
        t=time.perf_counter_ns()
        work()
        sync()
        samples.append((time.perf_counter_ns()-t)/1e6)
    return {key:float(np.percentile(samples,q)) for key,q in [("p50_ms",50),("p95_ms",95),("p99_ms",99)]}


def run(device,repeats):
    if device=="cuda":
        try:
            import cupy as xp
        except ImportError:
            print("CUDA mode requires CuPy for this driver; install the 'gpu' extra "
                  "(pip install 'neuroscifi[gpu]')", file=sys.stderr)
            raise SystemExit(2) from None
        sync=xp.cuda.Stream.null.synchronize
        hardware=xp.cuda.runtime.getDeviceProperties(0)["name"]
        hardware=hardware.decode() if isinstance(hardware,bytes) else str(hardware)
    else:
        xp=np
        def sync():
            return None
        hardware=platform.machine()
    rng=np.random.default_rng(53875)
    width=256
    steps=12
    weights=(rng.standard_normal((width,width))/np.sqrt(width)).astype(np.float32)
    bias=(.15*rng.standard_normal(width)).astype(np.float32)
    w,b=xp.asarray(weights),xp.asarray(bias)
    rows={}
    for batch in [1,8,64]:
        x=rng.standard_normal((batch,width)).astype(np.float32)
        xd=xp.asarray(x)
        reference=rollout(np,x,weights,bias,steps)
        out=rollout(xp,xd,w,b,steps)
        check=xp.asnumpy(out) if device=="cuda" else out
        if not np.allclose(check,reference,rtol=3e-4,atol=3e-5):
            raise ArithmeticError("Device result disagrees with NumPy float32 reference")
        def resident():
            return rollout(xp,xd,w,b,steps)
        def roundtrip():
            return xp.asnumpy(rollout(xp,xp.asarray(x),w,b,steps))
        block={"resident":clock(resident,sync,repeats),"max_abs_error":float(np.max(np.abs(check-reference)))}
        if device=="cuda":
            block["host_input_and_output"]=clock(roundtrip,sync,repeats)
        rows[str(batch)]=block
    return {"device":device,"hardware":hardware,"dtype":"float32","state_width":width,"steps":steps,"repetitions":repeats,"batches":rows}


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--device",choices=["cpu","cuda"],default="cpu")
    p.add_argument("--repeats",type=int,default=120)
    p.add_argument("--output",type=Path)
    a=p.parse_args()
    if a.repeats<1:
        p.error("repeats must be positive")
    payload=json.dumps(run(a.device,a.repeats),indent=2)+"\n"
    if a.output:
        a.output.write_text(payload)
    else:
        print(payload,end="")
