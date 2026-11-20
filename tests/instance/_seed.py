"""Local helpers: seed evidence rows directly, without depending on evidence.py."""
T0 = 1_800_000_000.0


def epoch(db, name, now=T0):
    db.execute("INSERT OR IGNORE INTO epochs(name,created_at) VALUES(?,?)", (name, now))


def add(db, id, *, epoch_name="e1", channel="word", root="partner-secret", bit=1, action=0,
        recorded_at=T0, expires_at=None, retention="standard", consent="learn"):
    epoch(db, epoch_name)
    db.execute("INSERT INTO evidence(id,epoch,channel,action,bit,root,consent,recorded_at,retention,expires_at) "
               "VALUES(?,?,?,?,?,?,?,?,?,?)",
               (id, epoch_name, channel, action, bit, root, consent, recorded_at, retention, expires_at))
    return db.execute("SELECT seq FROM evidence WHERE id=?", (id,)).fetchone()[0]


def pending(db, epoch_name="e1", now=T0):
    db.execute("INSERT INTO pending(epoch,kind,scope,question,created_at) VALUES(?,?,?,?,?)",
               (epoch_name, "ask", "mechanism", "which way?", now))
