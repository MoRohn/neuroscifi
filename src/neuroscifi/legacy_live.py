"""Local persistent model of a binary action, two senses, and a physical reference."""
import argparse
import json
from pathlib import Path
import sqlite3
import numpy as np
from .lab.core import CAMERA, SPEAKER, STATES, probabilities, update
from .lab.source_groups import group_log_likelihood


def open_store(path):
    db=sqlite3.connect(path,timeout=10)
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS epochs (name TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS observations (
            id TEXT PRIMARY KEY, epoch TEXT NOT NULL REFERENCES epochs(name),
            channel TEXT NOT NULL, action INTEGER NOT NULL, bit INTEGER NOT NULL,
            source_root TEXT NOT NULL);
    """)
    return db


def add_event(db,epoch,event_id,channel,action,bit,root):
    if channel not in ("camera","word","reference") or action not in (0,1) or bit not in (0,1):
        raise ValueError("Choose a supported channel and binary action and observation")
    if not event_id.strip() or not root.strip():
        raise ValueError("Event and source root must be present")
    with db:
        vals=(event_id,epoch,channel,action,bit,root)
        db.execute("INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?,?)",vals)
        stored=db.execute("SELECT * FROM observations WHERE id=?",(event_id,)).fetchone()
        if stored!=vals:
            raise ValueError("Event ID is already associated with a different observation")


def status(db,epoch):
    if not db.execute("SELECT 1 FROM epochs WHERE name=?",(epoch,)).fetchone():
        raise ValueError("Unknown epoch. Start it before recording observations")
    scores=np.zeros((1,8))
    references={}
    records=db.execute("SELECT channel,action,bit,source_root FROM observations WHERE epoch=? ORDER BY id",(epoch,)).fetchall()
    for channel,action,bit,root in records:
        corrected=np.array([bit^action])
        if channel=="reference":
            references.setdefault(root,[]).append(corrected)
        else:
            update(scores,CAMERA if channel=="camera" else SPEAKER,corrected,.1)
    for group in references.values():
        scores+=group_log_likelihood(np.array(group),.15,.10)
    beliefs=probabilities(scores)[0]
    margins=beliefs @ STATES
    return {"epoch":epoch,"stored_observations":len(records),"reference_roots":len(references),
            "probability_inverted":dict(zip(["mechanism","camera","speaker_convention"],margins.tolist())),
            "state_probabilities_in_binary_order":beliefs.tolist(),"top_state_probability":float(beliefs.max()),
            "suggested_next_observation":("try a known mechanical reference from a distinct root" if abs(margins[0]-.5)<.37 else "test a new action and check its observed consequence")}


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--db",type=Path,default=(Path("connectome-one.sqlite") if Path("connectome-one.sqlite").is_file() and not Path("neuroscifi.sqlite").exists() else Path("neuroscifi.sqlite")))
    sub=cli.add_subparsers(dest="op",required=True)
    for name in ["start","observe","status"]:
        sub.add_parser(name)
    for cmd in sub.choices.values():
        cmd.add_argument("epoch")
    obs=sub.choices["observe"]
    obs.add_argument("id")
    obs.add_argument("channel",choices=["camera","word","reference"])
    obs.add_argument("action",type=int,choices=[0,1])
    obs.add_argument("bit",type=int,choices=[0,1])
    obs.add_argument("root")
    a=cli.parse_args()
    db=open_store(a.db)
    try:
        if a.op=="start":
            with db:
                db.execute("INSERT OR IGNORE INTO epochs VALUES (?)",(a.epoch,))
        elif a.op=="observe":
            add_event(db,a.epoch,a.id,a.channel,a.action,a.bit,a.root)
        print(json.dumps(status(db,a.epoch),indent=2))
    except (ValueError,sqlite3.Error) as e:
        cli.exit(2,str(e)+"\n")
    finally:
        db.close()


if __name__=="__main__":
    main()
