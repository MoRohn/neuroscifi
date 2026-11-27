"""What a decode's result means for a question about a file, in ONE table (fix lane F1, rulings G and C).

Everything that answers on a file's behalf reads this table: the CLI's exit codes (`nsf inspect`, `nsf artifact
add | make | facts | forget | relate | feedback`) and the benchmark's driver (`acceptance/omni/subject.py`, which
only translates). The table states the product's decisions; nothing downstream decides again.

Outcomes:
- "answered": the question was answered (a flagged file asked about with `refuse_flagged=False` is answered with
  its content AND the flag: `Decoded.flagged`).
- "cannot": nothing here can do it, for a reason about this host's capabilities (no adapter reads the format, a
  module is missing): `ArtifactError.reason` in CANNOT_REASONS.
- "refused": a rule or limit said no (too large, malformed, unsafe, over time, words addressed to an AI agent), or
  only part of the file was read and the owner did not agree to a partial answer (`partial_ok`).

Exit codes (one table for every command): 0 answered / done; 1 cannot (the thing asked could not be done for a
reason about the input or this host); 2 refused, or misuse of the command.
"""
from __future__ import annotations

ANSWERED, CANNOT, REFUSED = "answered", "cannot", "refused"
OUTCOMES = (ANSWERED, CANNOT, REFUSED)
CANNOT_REASONS = frozenset({"unsupported", "missing_capability"})
EXIT_CODES = {ANSWERED: 0, CANNOT: 1, REFUSED: 2}
MISUSE = 2


def of_error(error) -> str:
    """The outcome of a decode (or a command) that raised `error` (an ArtifactError; any other error is a refusal)."""
    return CANNOT if (getattr(error, "reason", "") or "") in CANNOT_REASONS else REFUSED


def of_decoded(decoded, *, partial_ok: bool = False) -> str:
    """The outcome of a question about the content of `decoded`: a partial decode is refused unless the owner agreed
    to an answer from what was read."""
    status = getattr(decoded, "status", None) or ("partial" if decoded.partial else "read")
    if status == "partial" and not partial_ok:
        return REFUSED
    return ANSWERED


def of_member(member, *, partial_ok: bool = False) -> str:
    """The outcome of a question about one member of a Bundle. The archive readers label each member's note with
    their own decision ("refused: ..." when a rule refused to decode it, "partial: ..." when a limit cut its decode
    short; `archives._Walk`); this table is where that label becomes an outcome, so nothing downstream reads the
    words. A member that was not decoded is "refused" when a rule refused it, else "cannot" (it was only listed, or
    its decode failed for a reason about its bytes); a partly decoded member is refused unless the owner agreed to a
    partial answer."""
    note = str(getattr(member, "note", "") or "")
    if getattr(member, "inner", None) is None:
        return REFUSED if note.startswith("refused") else CANNOT
    if note.startswith("partial") and not partial_ok:
        return REFUSED
    return ANSWERED


def member_path(path) -> str | None:
    """The name a question may give a Bundle member by: its path with a leading "./" dropped; None for a climbing
    ("..") or absolute path, which names no member (fix lane F4, measurement R2-4: the product says it, not the
    benchmark's driver; the archive readers already refuse such members, so no answer is ever given about one by
    name)."""
    path = str(path)
    path = path[2:] if path.startswith("./") else path
    if path.startswith("/") or ".." in path.split("/"):
        return None
    return path


def exit_code(outcome: str) -> int:
    return EXIT_CODES[outcome]


__all__ = ["ANSWERED", "CANNOT", "CANNOT_REASONS", "EXIT_CODES", "MISUSE", "OUTCOMES", "REFUSED", "exit_code",
           "member_path", "of_decoded", "of_error", "of_member"]
