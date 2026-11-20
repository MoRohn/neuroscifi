"""Select a frozen graph using past observations. No environment label enters the selector."""
import argparse
import json
from collections import deque
from pathlib import Path
import numpy as np
from .graph_bench import simulate, stream
from ..graph_store import encode, decode


class GraphRecall:
    def __init__(self, snapshots, history=12):
        if not snapshots or history < 1:
            raise ValueError("Provide at least one snapshot and one recent observation")
        self.graphs = np.stack([decode(blob) for blob in snapshots])
        self.errors = deque(maxlen=history)
        self.initial = len(snapshots)-1

    def predict(self, observation):
        predictions = self.graphs @ observation
        chosen = self.initial if not self.errors else int(np.argmin(np.sum(self.errors, axis=0)))
        return chosen, predictions

    def observe(self, predictions, outcome):
        self.errors.append(np.mean((predictions - outcome)**2, axis=1))


def evaluate(count=64):
    totals=np.zeros(4)
    samples=0
    for seed in range(840091,840091+count):
        worlds=[]
        snapshots=[]
        def save(epoch, world, dense, growing):
            worlds.append(world)
            snapshots.append(encode(dense, 3, 4))
        simulate(seed, observe_epoch=save)
        recall=GraphRecall(snapshots)
        rng=np.random.default_rng(seed+700000)
        # Start from the most recent world and change three times; keep history across boundaries.
        for context in (1,0,1,0):
            observations,targets=stream(worlds[context],128,rng)
            for x,y in zip(observations,targets):
                chosen,pred=recall.predict(x)
                err=np.mean((pred-y)**2,axis=1)
                totals += [err[chosen],err[-1],err[context],chosen==context]
                samples+=1
                recall.observe(pred,y)
    vals=totals/samples
    return dict(zip(["selected_mse","latest_only_mse","oracle_mse","context_accuracy"],map(float,vals))) | {
        "world_pairs":count,"scored_steps":samples,"history":12,"snapshot_bits":4,"retained_inputs_per_node":3}


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",type=Path)
    a=p.parse_args()
    s=json.dumps(evaluate(),indent=2)+"\n"
    if a.output: a.output.write_text(s)
    else: print(s,end="")
