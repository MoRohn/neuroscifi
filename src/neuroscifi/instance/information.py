"""Exact expected information gain of the questions the executive could ask, a rule version that asks the most
informative one, and whether a decision came from a matched earlier setup (Project Cognitive Genesis, lane I;
ADR-051). Pure reads: nothing here writes, and beliefs are computed exactly as before (beliefs._assess).

gains(db, epoch)   For each subject a question could be about (one more reference reading from a new root: the
                   mechanism; each camera root; each word partner) the mutual information, in bits, between the
                   hidden bits of the belief model and the next READINGS readings of that subject:
                       EIG = H(Y) - H(Y | hidden bits)
                   H(Y) is exact: the probability of each possible sequence of readings is the ratio of the model's
                   marginal likelihood with and without it (beliefs._infer on the tallies plus the hypothetical
                   readings, the way the change detector rescores hypothetical counts). H(Y | hidden bits) is the
                   reading noise: READINGS x h(0.10) for camera and word readings; for a new reference root, whose
                   fixture polarity is integrated out (flip 0.15, error 0.10), the entropy of that mixture.
                   Beside it, `p_informative`: a check of the model against the subject's own readings. Two kinds of
                   reporter are compared on them: one the belief model assumes (reading error 0.10, either relation)
                   and an uninformative one (coin flips), with prior P_INFORMATIVE. A partner whose words are as often
                   one bit as the other over many readings is far likelier the second: its readings cannot teach
                   anything however uncertain its belief stays (the noisy TV). `score` = EIG x p_informative.
                   Shown beside every decision (`nsf decide`, `nsf explain`) without changing it.
RULES              "rules-v1" is the executive's fixed rule order (executive._policy), unchanged. "information-v1"
                   keeps rules 1-4b (writes paused, change suspected, no evidence, an earlier setup matches) and
                   replaces 5-7 (reference when the mechanism is unidentifiable, then the most ambiguous partner,
                   else act) by: the subject with the greatest score, if it reaches MIN_SCORE bits; else act. A
                   partner is scored by what its next words teach about its own convention (`own_bits`, not the
                   mechanism) and is asked only while its meaning is ambiguous as rule 6 defines it (at least 4
                   words, |p - .5| < .37); a partner question already answered is not asked again within 3 new events
                   (as in rules-v1). The owner adopts a version with `nsf config --executive-rule`. (information-v1
                   was amended before any release, review1 Q1: it asked partners whose meaning was settled, for what
                   their words teach about the mechanism, and partners with fewer than 4 words.)
processing(...)    Habitual or deliberate: a decision in an epoch that continues a matched earlier setup (an epoch
                   link, rule 4b answered "continue") reuses that setup's calibration (habitual); otherwise it rests on
                   the epoch's own evidence (deliberate). A change suspected in a linked epoch means the habit may no
                   longer apply. report() gives the rates over the recorded decisions.
"""
from __future__ import annotations

from math import comb, exp, log, log2

from . import beliefs

READINGS = 3              # the readings one question (one investigation) is assumed to bring
P_INFORMATIVE = 0.5       # prior that a camera or partner reports as the belief model assumes (vs coin flips)
MIN_SCORE = 0.01          # bits: below this nothing is worth asking and information-v1 acts (chosen on development)
NEW_REFERENCE = "(a new reference root)"


def _h(p) -> float:
    return 0.0 if p <= 0.0 or p >= 1.0 else -(p * log2(p) + (1 - p) * log2(1 - p))


def _noise_entropy(channel, k) -> float:
    """H(Y | hidden bits) of k readings, in bits."""
    if channel != "reference":
        return k * _h(beliefs.READING_ERROR)
    f, e = beliefs.REFERENCE_FLIP, beliefs.REFERENCE_ERROR
    total = 0.0
    for j in range(k + 1):              # sequences with j readings agreeing with the mechanism
        p = (1 - f) * (1 - e) ** j * e ** (k - j) + f * e ** j * (1 - e) ** (k - j)
        total -= comb(k, j) * p * log2(p)
    return total


def _with(factors, key, m, n0, n1):
    """factors plus n0 zeros and n1 ones for `key` in mechanism segment m (a new factor when key is absent)."""
    out, found = [], False
    for k, counts in factors:
        if k == key:
            counts = dict(counts)
            a, b = counts.get(m, (0, 0))
            counts[m] = (a + n0, b + n1)
            found = True
        out.append((k, counts))
    if not found:
        out.append((key, {m: (n0, n1)}))
    return out


def _own(inference, key) -> float:
    """P(the factor's own bit = 1) under an inference (0.5 for a factor it does not hold)."""
    given = inference["conditional"].get(key)
    return 0.5 if given is None else beliefs._factor_p(inference, given[0])


def _eig_parts(factors, segments, key, k=READINGS) -> tuple[float, float]:
    """(joint, own): the exact mutual information (bits) between k readings of `key` and all the hidden bits (see the
    module doc), and between them and the factor's own bit alone (a camera's alignment, a partner's convention):
    H(own) - E_Y H(own | Y), from the same exact posteriors."""
    inference = beliefs._infer(factors, segments)
    base, m = inference["log_marginal"], segments - 1
    h_y, h_own = 0.0, 0.0
    for j in range(k + 1):              # j readings of 1; each such sequence has the same probability
        after = beliefs._infer(_with(factors, key, m, k - j, j), segments)
        p = exp(after["log_marginal"] - base)
        if p > 0:
            h_y -= comb(k, j) * p * log2(p)
            h_own += comb(k, j) * p * _h(_own(after, key))
    joint = max(0.0, h_y - _noise_entropy(key[0], k))
    return joint, (0.0 if key[0] == "reference" else max(0.0, _h(_own(inference, key)) - h_own))


def _eig(factors, segments, key, k=READINGS) -> float:
    """Exact mutual information (bits) between the hidden bits and k readings of `key` (see the module doc)."""
    return _eig_parts(factors, segments, key, k)[0]


def _p_informative(counts) -> float:
    """Posterior that a reporter with these readings reports as the belief model assumes rather than at random.

    Per mechanism segment (review1 CM4): an informative reporter's readings in segment m equal w_m XOR its own bit,
    and the mechanism bits of the segments are independent and uniform under the model's prior, so its readings of
    each segment agree among themselves (either way) with error READING_ERROR, independently of the other segments.
    Pooling the segments called a reporter whose readings flip exactly when the mechanism changed a coin."""
    hit, miss = log(1 - beliefs.READING_ERROR), log(beliefs.READING_ERROR)
    model = coin = 0.0
    for n0, n1 in counts.values():
        model += log(0.5) + beliefs._lse(n1 * hit + n0 * miss, n1 * miss + n0 * hit)    # in logs: no underflow
        coin += (n0 + n1) * log(0.5)
    return beliefs._logistic(model - coin + log(P_INFORMATIVE / (1 - P_INFORMATIVE)))


def assessed_gains(assessed, k=READINGS) -> list[dict]:
    """gains() from an assessment already made (beliefs._assess's tuple)."""
    result, _, factors, _ = assessed
    segments = len(result["segments"].get("mechanism") or [None])
    current = {}
    for key, counts in factors:
        channel, root, fseg = key
        if channel != "reference" and ((channel, root) not in current or fseg > current[(channel, root)][0][2]):
            current[(channel, root)] = (key, counts)
    out = [{"subject": "reference", "scope": "mechanism", "decision": "test_reference",
            "eig_bits": _eig(factors, segments, ("reference", NEW_REFERENCE, 0), k), "p_informative": 1.0}]
    for (channel, root), (key, counts) in sorted(current.items()):
        scope = beliefs.scope_of(channel, root)
        joint, own = _eig_parts(factors, segments, key, k)
        row = {"subject": scope, "scope": scope, "decision": "ask" if channel == "word" else "act",
               "eig_bits": joint, "p_informative": _p_informative(counts),
               "readings": sum(a + b for a, b in counts.values())}
        if channel == "word":
            row["own_bits"] = round(own, 6)
        out.append(row)
    for row in out:
        # A question to a partner is scored by what it teaches about that partner's own convention (review1 Q1):
        # its words also inform the mechanism, but that is the reference readings' question, not the partner's.
        row["score"] = round(row.get("own_bits", row["eig_bits"]) * row["p_informative"], 6)
        row["eig_bits"] = round(row["eig_bits"], 6)
        row["p_informative"] = round(row["p_informative"], 6)
    out.sort(key=lambda r: (-r["score"], r["subject"]))
    return out


def gains(db, epoch, k=READINGS) -> dict:
    """{"readings_per_question", "candidates": [...] (best first), "note"} for the epoch (see the module doc)."""
    assessed = beliefs._assess(db, epoch, detect=False, legacy=False)
    if not assessed[0]["evidence"]["learning"]:
        return {"readings_per_question": k, "candidates": [], "note": "no learning evidence yet"}
    return {"readings_per_question": k, "candidates": assessed_gains(assessed, k),
            "note": f"exact mutual information (bits) between the hidden bits and the next {k} readings of each "
                    "subject; score = that x P(the subject's readings are informative)"}


def information_policy(db, epoch, result, assessed, horizon, answered):
    """Rules 5-7 of information-v1: (decision, pending kind, scope, reason, question)."""
    through = f" (evidence through #{horizon})"
    ranked = assessed_gains(assessed)
    skipped = []
    for row in ranked:
        if row["score"] < MIN_SCORE:
            break
        where = (f"score {row['score']:.3f} bits = expected gain {row['eig_bits']:.3f} bits of {READINGS} readings "
                 f"x P(informative) {row['p_informative']:.2f}")
        if row["decision"] == "test_reference":
            return ("test_reference", "test_reference", "mechanism",
                    f"information-v1: a new reference root is the most informative question ({where}); "
                    f"{beliefs.LEGACY_REFERENCE_TEXT}",
                    f"Please {beliefs.LEGACY_REFERENCE_TEXT} (the most informative next reading).{through}")
        if row["decision"] == "ask":
            # A partner is asked only while its own meaning is ambiguous, as rules-v1 defines it (at least
            # MIN_PARTNER_WORDS words, p_inverted within IDENTIFIABLE_MARGIN of 0.5; review1 Q1): its words would
            # otherwise be asked for what they teach about something else, or before it has said enough.
            from .executive import MIN_PARTNER_WORDS
            root = row["scope"].split(":", 1)[1]
            partner = result["partners"].get(root) or {"p_inverted": 0.5, "n": 0}
            if beliefs._identifiable(partner["p_inverted"]):
                skipped.append(f"{row['scope']} (its meaning is settled)")
                continue
            if partner["n"] < MIN_PARTNER_WORDS:
                skipped.append(f"{row['scope']} (fewer than {MIN_PARTNER_WORDS} words)")
                continue
            done = answered(db, epoch, "ask", row["scope"])
            if done is not None:
                skipped.append(f"{row['scope']} (question #{done} answered)")
                continue
            return ("ask", "ask", row["scope"],
                    f"information-v1: {row['scope']} is the most informative question ({where}); ask",
                    f"What does {root} mean by their words? Their next words are the most informative reading "
                    f"now.{through}")
        return ("act", None, row["scope"],
                f"information-v1: readings of {row['scope']} are the most informative ({where}); "
                f"{beliefs.LEGACY_ACT_TEXT}", None)
    p = result["mechanism"]["p_inverted"]
    note = f"; not asked: {', '.join(skipped)}" if skipped else ""
    return ("act", None, None,
            f"information-v1: no question reaches {MIN_SCORE} bits (mechanism p_inverted={p:.3f}); "
            f"{beliefs.LEGACY_ACT_TEXT}{note}", None)


# ---- habitual or deliberate ------------------------------------------------------------------------------------------

def processing(db, epoch, change=None) -> dict:
    """{"mode": "habitual" | "deliberate", "why", "habit_applies": True | False | None} for a decision now."""
    linked = beliefs.link(db, epoch)
    if linked is None:
        return {"mode": "deliberate", "why": "the epoch's own evidence (it continues no earlier setup)",
                "habit_applies": None}
    out = {"mode": "habitual", "why": f"continues the calibration of {linked['source_epoch']} (a matched earlier "
                                      f"setup, question #{linked['pending_id']})", "habit_applies": True}
    if change and change.get("suspected"):
        out["habit_applies"] = False
        out["why"] += (f"; the habit may no longer apply: a change of {change['scope']} is suspected since evidence "
                       f"#{change['since_seq']}")
    return out


def report(db) -> dict:
    """Rates over the recorded decisions: habitual (made while the epoch continued an earlier setup) or deliberate,
    and how often a linked epoch's habit stopped applying (a change question raised, or a change the owner
    confirmed, after the link)."""
    if not beliefs._has_table(db, "epoch_links"):
        links = {}
    else:
        links = {e: (at, pid) for e, at, pid in db.execute("SELECT epoch, created_at, pending_id FROM epoch_links")}
    total = habitual = 0
    for epoch, at in db.execute("SELECT epoch, created_at FROM decisions"):
        total += 1
        if epoch in links and links[epoch][0] <= at:
            habitual += 1
    suspected = confirmed = 0
    for epoch, (at, _) in links.items():
        suspected += db.execute("SELECT count(*) FROM pending WHERE epoch=? AND kind='calibrate' AND created_at>=?",
                                (epoch, at)).fetchone()[0]
        if beliefs._has_changepoints(db):
            confirmed += db.execute("SELECT count(*) FROM changepoints WHERE epoch=? AND confirmed=1 AND "
                                    "created_at>=?", (epoch, at)).fetchone()[0]
    return {"decisions": total, "habitual": habitual, "deliberate": total - habitual,
            "habitual_share": round(habitual / total, 6) if total else None, "linked_epochs": len(links),
            "habit_stopped_applying": {"change_questions_after_link": suspected,
                                       "changes_confirmed_after_link": confirmed}}


__all__ = ["MIN_SCORE", "P_INFORMATIVE", "READINGS", "assessed_gains", "gains", "information_policy", "processing",
           "report"]
