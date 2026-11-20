"""Rebuild the deterministic evidence in a new directory and compare to published values."""
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

BASE=Path(__file__).resolve().parent
PAIRS=[("core","reference"),("sensory","sensory"),("recalibrate","recalibration"),
       ("source_groups","lineage"),("graph_bench","graph"),("human_counts","human_counts"),("graph_compression_bench","graph_packing"),("context_recall","recall")]


def equal(a,b):
    if isinstance(a,dict):
        return a.keys()==b.keys() and all(equal(v,b[k]) for k,v in a.items())
    if isinstance(a,list):
        return len(a)==len(b) and all(equal(x,y) for x,y in zip(a,b))
    if isinstance(a,float):
        return math.isclose(a,b,abs_tol=1e-9,rel_tol=1e-9)
    return a==b


if __name__=="__main__":
    env={**os.environ,"OPENBLAS_NUM_THREADS":"1","OMP_NUM_THREADS":"1"}
    with tempfile.TemporaryDirectory() as directory:
        for module,name in PAIRS:
            script=BASE/"src"/(module+".py")
            result=Path(directory)/(name+".json")
            if module=="human_counts":
                with result.open("w") as dest:
                    subprocess.run([sys.executable,str(script)],stdout=dest,env=env,check=True)
            else:
                subprocess.run([sys.executable,str(script),"--output",str(result)],env=env,check=True)
            expected=json.loads((BASE/"evidence"/(name+".json")).read_text())
            if not equal(json.loads(result.read_text()),expected):
                raise SystemExit("Numerical mismatch in "+name)
            print("Verified "+name,flush=True)
