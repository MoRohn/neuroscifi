"""Heuristic mapping from a research report to NeuroSciFi's own documented learning architecture (live scenario B).

This is a keyword/section matcher, not reasoning. For each theme it lists report findings whose text matches the
theme's web patterns (with their citations) and the repository passages (Architecture.md paragraphs under their
heading, REQUIREMENTS_MATRIX.md rows) that match its repo patterns, and emits a PROPOSAL when both sides exist.
Nothing is changed: proposals are text in the benchmark output for the owner to read. A match means "these
passages share vocabulary", not "the evidence bears on the design".
"""
from __future__ import annotations

from pathlib import Path
import re

METHOD = ("heuristic keyword/section mapping (deterministic); proposals only, nothing in the system is modified; "
          "a match means shared vocabulary, not an established implication")
REPO_DOCS = ("Architecture.md", "docs/architecture/REQUIREMENTS_MATRIX.md")

THEMES = (
    {"id": "dendritic-nonlinearity", "label": "nonlinear dendritic computation",
     "web": (r"\bdendrit\w*",), "repo": (r"\bdendrit\w*",)},
    {"id": "compartment-plasticity", "label": "branch- or compartment-specific plasticity rules",
     "web": (r"\bcompartment\w*", r"\bbranch(?:es)?\b.*\bplasticit\w*|\bplasticit\w*.*\bbranch(?:es)?\b"),
     "repo": (r"\bbranch[- ]specific\b|\bcompartment\w*", r"\bbranch type\b|\bbranch rules?\b")},
    {"id": "eligibility-credit", "label": "eligibility traces and credit assignment",
     "web": (r"\beligibility\b", r"\bcredit assignment\b", r"\be-?prop\b"),
     "repo": (r"\beligibility[- ]trace", r"\be-prop\b", r"\bcredit and stability\b")},
    {"id": "spike-timing", "label": "spike-timing-dependent plasticity",
     "web": (r"\bspike[- ]timing\b", r"\bSTDP\b"), "repo": (r"\bSTDP\b", r"\bpre-before-post\b")},
    {"id": "neuron-as-network", "label": "single neurons as multi-layer computing units",
     "web": (r"\bsingle neurons?\b.*\b(?:network|layer)s?\b", r"\b(?:two|multi)-layer\b"),
     "repo": (r"\bneuron has internal dynamics\b", r"\bactivation and a scalar parameter\b")},
)


def _paragraphs(text: str):
    """(line number, heading, paragraph) for every blank-line separated block; headings are '#' lines."""
    heading, start, block = "", 1, []
    for n, line in enumerate(text.splitlines() + [""], start=1):
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            continue
        if line.strip():
            if not block:
                start = n
            block.append(line.strip())
        elif block:
            yield start, heading, " ".join(block)
            block = []


def repo_passages(root: Path) -> list[dict]:
    out = []
    for rel in REPO_DOCS:
        path = Path(root) / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if rel.endswith("REQUIREMENTS_MATRIX.md"):
            for n, line in enumerate(text.splitlines(), start=1):
                m = re.match(r"^\|\s*([A-Z]{2,4}-\d+[a-z]?)\s*\|", line)
                if m:
                    cells = [c.strip() for c in line.strip().strip("|").split("|")]
                    out.append({"file": rel, "line": n, "heading": m.group(1), "text": " | ".join(cells)})
        else:
            for n, heading, para in _paragraphs(text):
                out.append({"file": rel, "line": n, "heading": heading, "text": para})
    return out


def _hits(patterns, text):
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _excerpt(text, patterns, width=220):
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            start = max(0, m.start() - width // 3)
            return ("…" if start else "") + text[start:start + width].strip() + ("…" if start + width < len(text)
                                                                                 else "")
    return text[:width]


def implications(report: dict, root: Path) -> dict:
    """Proposals linking `report` findings to repository passages, theme by theme (see module docstring)."""
    by_n = {s["n"]: s for s in report.get("sources") or []}
    statements = [{"text": f.get("statement") or "", "citations": [by_n[n]["url"] for n in f.get("citations") or []
                                                                   if n in by_n],
                   "status": f.get("status"), "section": f.get("section")}
                  for f in report.get("findings") or []]
    passages = repo_passages(root)
    themes, proposals = [], []
    for theme in THEMES:
        web = [s for s in statements if _hits(theme["web"], s["text"])]
        repo = sorted((p for p in passages if _hits(theme["repo"], p["text"])),      # requirement rows first
                      key=lambda p: (not p["file"].endswith("REQUIREMENTS_MATRIX.md"), p["file"], p["line"]))
        requirement_ids = sorted({p["heading"] for p in repo if re.fullmatch(r"[A-Z]{2,4}-\d+[a-z]?", p["heading"])})
        entry = {"theme": theme["id"], "label": theme["label"], "web_findings": len(web), "repo_passages": len(repo),
                 "requirements": requirement_ids,
                 "web_evidence": [{"statement": s["text"][:300], "status": s["status"], "sources": s["citations"][:3]}
                                  for s in web[:3]],
                 "repo_anchors": [{"file": p["file"], "line": p["line"], "heading": p["heading"],
                                   "excerpt": _excerpt(p["text"], theme["repo"])} for p in repo[:4]]}
        themes.append(entry)
        if web and repo:
            anchors = ", ".join(f"{a['file']}:{a['line']}" for a in entry["repo_anchors"])
            reqs = f" (requirements {', '.join(requirement_ids)})" if requirement_ids else ""
            proposals.append({
                "theme": theme["id"], "status": "proposal_only",
                "text": (f"PROPOSAL (not applied): {len(web)} report finding(s) on {theme['label']} share vocabulary "
                         f"with {len(repo)} passage(s) of the documented architecture{reqs} at {anchors}. The owner "
                         f"may review whether this evidence should inform the deferred research agenda; no "
                         f"component, requirement status or configuration was changed."),
                "web_sources": sorted({u for s in web for u in s["citations"]})[:6],
                "repo_anchors": [f"{a['file']}:{a['line']}" for a in entry["repo_anchors"]]})
        elif web:
            proposals.append({"theme": theme["id"], "status": "proposal_only",
                              "text": (f"PROPOSAL (not applied): {len(web)} report finding(s) on {theme['label']} have no "
                                       f"matching passage in the documented architecture; the owner may consider "
                                       f"whether this is a gap worth a requirement or an explicit non-goal."),
                              "web_sources": sorted({u for s in web for u in s["citations"]})[:6], "repo_anchors": []})
    return {"method": METHOD, "documents": list(REPO_DOCS), "themes": themes, "proposals": proposals,
            "findings_considered": len(statements)}
