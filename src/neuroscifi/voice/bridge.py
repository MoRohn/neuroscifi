"""Cognitive Voice Bridge: a final utterance -> the same research path text uses -> an approved reply -> speech.

NeuroSciFi is authoritative. A question becomes a research task through webcortex.orchestrator.submit (privacy
screen, web switch, budgets, deduplication, the worker's discovery/reading/verification, the inference gateway's
evidence-bound answer and its guard, consolidation into memory) - exactly what the console's text box does. The
bridge never asks a model what is true; it reads the task's report. What is spoken is a short rendering of text
that already passed the evidence guard (model answer) or is labelled extractive/template; the detailed text with
citations goes to the transcript and screen.

Authorisation: the principal decides what may run. The owner console may do everything below; a Pocket device only
what Pocket already may (Pocket voice exists only while the pocket_research switch is on: research, its own
family's tasks, its own transcript, mode, and asking the microphone's holder for a hand-off); reading the knowledge
base ("what have you learned") is owner-only. A Pocket device changes research (asks, cancels, continues) under the
rules of the HTTP research routes (pocket/research_routes.py, ADR-042): a device enrolled as a signed research node
must sign its changes, and a spoken or typed command carries no node signature, so voice refuses them there and
points to the Research panel's signed controls (reading, explaining and showing research stay open); and no device
cancels or continues a research session another device owns (nodes.may_drive). Sensitive commands - forget/delete, enabling autonomy, disclosing
private terms to the web, moving the microphone to another device, accepting conclusions into memory - return a
confirmation request; nothing happens until the same session confirms it (voice "confirm" or the Confirm button)
within CONFIRM_SECONDS.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import secrets
import threading

from ..instance.errors import Conflict, InstanceError, NotFound, ValidationError
from . import commands

CONFIRM_SECONDS = 60.0
# Research started from conversation (voice or typed), per principal: at most this many new tasks in the window
# (UAT T5: 385 tasks were queued in about 5 s). Only tasks created count (a deduplicated repeat creates none).
CONVERSATION_RESEARCH_LIMIT = 8
CONVERSATION_RESEARCH_WINDOW = 600.0
MAX_SPOKEN_SENTENCES = 2
HISTORY_ITEMS = 5
MAX_SPOKEN_WORDS = 70
LABEL_TEMPLATE = "NeuroSciFi template reply (no model involved)"
LABEL_EXTRACTIVE = "Extractive summary (quoted evidence); advanced reasoning unavailable"
# A model answer's label says what the transcript shows (T4-02): every sentence passed the citation guard, and a
# sentence that rests on a claim independent sources do not support carries a mark (STATE_MARKS) after its citations.
LABEL_MODEL = ("Model-written summary by {model} from the collected evidence; each sentence was checked against its "
               "cited sources, and a sentence that independent sources do not support is marked")
STATE_MARKS = {"insufficient": "only one source or too little evidence; not independently confirmed",
               "temporally_unresolved": "not established as current", "conflicting": "sources conflict",
               "disputed": "disputed by other sources", "not_evaluated": "not checked against other sources"}
# A command recognised only by spelling similarity (commands.FUZZY_MIN) runs after a confirmation when it does more
# than stop or resume speech or show something; the sensitive commands ask for confirmation in any case.
CONFIRM_IF_FUZZY = {"cancel_research": "cancel the research that is running",
                    "continue_research": "continue the previous investigation",
                    "mode": "change the voice mode", "disable_autonomy": "turn off autonomous research"}
# A Pocket device enrolled as a signed research node (security review A-1): voice cannot sign, so it reads only.
NODE_SIGNED = ("This device is a research node, so your Spark accepts research changes from it only when they are "
               "signed with its key, and a spoken or typed command carries no signature. Use the Research panel to "
               "ask, cancel or continue; here I can read, show and explain research.")
SESSION_OWNED = "Another of your devices is driving this research right now. Ask it to hand it over."
HELP = ("You can ask a question or say: research followed by a topic, show evidence, explain more simply, "
        "remember conclusions, what have you learned since yesterday, what was I researching, continue previous "
        "investigation, stop, "
        "stop speaking but keep researching, switch to text only, or speak your answers.")


@dataclass
class Principal:
    """Who is speaking, and what they may do (decided by the server that authenticated the request)."""
    name: str                       # 'owner' or 'pocket:<family tag>'
    owner: bool
    research: bool                  # may submit research
    origin: str = "voice"
    family: str | None = None       # Pocket family digest (task scoping), None for the owner
    admit: object = None            # Pocket rate limit: admit(callable) -> result or None when over the limit
    label: str = "Owner console"
    device_prefix: str = "console:"   # capture-device ids are namespaced by the server, never chosen by a client
    device_fixed: str | None = None   # Pocket: the device is the paired credential itself (client id ignored)
    node_device: str | None = None    # Pocket: the salted device digest the research routes use (nodes.device_digest)


@dataclass
class Reply:
    intent: str
    speech: list = field(default_factory=list)     # concise sentences to speak (mode A only)
    text: str = ""                                  # detailed text for the transcript/screen
    source: str = "template"                        # 'model' (passed the evidence guard) or 'template'
    label: str = LABEL_TEMPLATE
    task_id: str | None = None
    sources: list = field(default_factory=list)     # [{n, title, url, domain}]
    confirm: dict | None = None                     # {id, action, summary, expires_at}
    actions: list = field(default_factory=list)     # client/hub actions
    follow: str | None = None                       # research task to follow until it finishes

    def public(self):
        return {"intent": self.intent, "speech": self.speech, "text": self.text, "source": self.source,
                "label": self.label, "task_id": self.task_id, "sources": self.sources,
                "confirm": None if self.confirm is None else {k: self.confirm[k] for k in
                                                              ("id", "action", "summary", "expires_at")},
                "actions": self.actions}


def _say(intent, text, **kw):
    return Reply(intent, speech=[text] if text else [], text=text, **kw)


_CITE = re.compile(r"\s*\[\d+(?:\s*,\s*\d+)*\]")


def spoken(sentences, limit=MAX_SPOKEN_SENTENCES, words=MAX_SPOKEN_WORDS) -> list[str]:
    """The first sentences, citation markers removed, within a word budget (speech is the concise rendering)."""
    out, n = [], 0
    for s in sentences:
        s = " ".join(_CITE.sub("", s).split())
        if not s:
            continue
        w = len(s.split())
        if out and (len(out) >= limit or n + w > words):
            break
        out.append(s)
        n += w
    return out


def _sources(report, numbers=None):
    out = []
    for s in (report or {}).get("sources") or []:
        if not isinstance(s, dict) or not isinstance(s.get("n"), int):
            continue
        if numbers is not None and s["n"] not in numbers:
            continue
        out.append({"n": s["n"], "title": str(s.get("title") or "")[:300], "url": str(s.get("url") or "")[:2000],
                    "domain": str(s.get("domain") or "")[:255]})
    return out[:20]


def _cited(item) -> str:
    """'text [1, 2]', or just the text when nothing is cited (never an empty '[]')."""
    numbers = [str(n) for n in item.get("citations") or []]
    return f"{item['text']} [{', '.join(numbers)}]" if numbers else item["text"]


def _marked(item) -> str:
    """The cited line with the mark of each claim state it rests on that is not "supported" (T4-02)."""
    marks = [STATE_MARKS.get(st, str(st).replace("_", " ")) for st in dict.fromkeys((item.get("states") or {}).values())
             if st != "supported"]
    return _cited(item) + (f" ({'; '.join(marks)})" if marks else "")


def compose_answer(task) -> Reply:
    """The reply for a finished research task, from its report only."""
    status, objective = task.get("status"), task.get("objective") or ""
    report = task.get("result") if isinstance(task.get("result"), dict) else None
    if status in ("failed", "cancelled") or report is None:
        why = {"cancelled": "was cancelled"}.get(status, "did not finish")
        err = (task.get("error") or "")[:200]
        return _say("research_result", f"The research on {objective[:120]} {why}." + (f" {err}" if err else ""),
                    task_id=task.get("id"))
    reasoning = report.get("reasoning") if isinstance(report.get("reasoning"), dict) else {}
    if reasoning.get("status") == "model" and reasoning.get("answer"):
        items = [a for a in reasoning["answer"] if isinstance(a, dict) and a.get("text")]
        lines = [_marked(a) for a in items]
        cited = {n for a in items for n in a.get("citations") or []}
        by = str(reasoning.get("generated_by") or report.get("generated_by") or "")
        model = by[6:] if by.startswith("model:") and by[6:] else "the local model"
        reply = Reply("research_result", speech=spoken([a["text"] for a in items]), text="\n".join(lines),
                      source="model", label=LABEL_MODEL.format(model=model[:120]),
                      task_id=task.get("id"), sources=_sources(report, cited))
    else:
        items = [a for a in report.get("answer") or [] if isinstance(a, dict) and a.get("text")]
        lead = [a for a in items if a.get("status") == "summary"]
        quotes = [a for a in items if a.get("status") != "summary"]
        by_n = {s["n"]: s for s in _sources(report)}
        speech = []
        unanswered = reasoning.get("unanswered") if isinstance(reasoning.get("unanswered"), dict) else {}
        unanswered = str(unanswered.get("text") or "").strip()[:400]
        if unanswered:
            # the guard rejected every model sentence: say what was not established, as the console does, instead of
            # a quote that may not address the question (T2-03); the quotes stay below as the evidence of record
            speech = spoken([unanswered], 1)
        elif quotes:
            first = quotes[0]
            domain = next((by_n[n]["domain"] for n in first.get("citations") or [] if n in by_n), "")
            speech = spoken([(f"According to {domain}: " if domain else "One source says: ") + first["text"]], 1)
        elif lead:
            speech = spoken([lead[0]["text"]], 1)
        lines = [_cited(a) for a in items]
        if unanswered:
            lines = [unanswered] + (["Quoted evidence (it may not answer the question):", *lines] if lines else [])
        label = LABEL_EXTRACTIVE
        if reasoning.get("reason"):
            label += f" ({str(reasoning['reason'])[:160]})"
        reply = Reply("research_result", speech=speech, text="\n".join(lines) or "No answer was found.",
                      source="template", label=label, task_id=task.get("id"), sources=_sources(report))
    note = reasoning.get("note") if isinstance(reasoning.get("note"), dict) else {}
    note = str(note.get("text") or "").strip()[:300]
    if note:
        # D-R5-3: the verdict did not evaluate or establish the checked statement; say so whatever the model wrote
        reply.text = note + "\n" + reply.text
        reply.speech = list(reply.speech) + [note]
    verdict = report.get("verdict") if isinstance(report.get("verdict"), dict) else None
    if verdict and verdict.get("label"):
        reply.text += f"\nVerdict: {verdict['label']}" + (f" - {verdict.get('statement')}"
                                                         if verdict.get("statement") else "")
    if status == "partial":
        reply.text += "\n(Partial result: the research stopped early; say 'continue previous investigation'.)"
    if reply.speech and len(reply.text) > sum(len(s) for s in reply.speech) + 40:
        reply.speech.append("The details and citations are on screen.")
    return reply


class Bridge:
    def __init__(self, clock):
        self.clock = clock
        self._research_lock = threading.Lock()   # the limit check and the task it admits are one step

    def _research_limited(self, db, principal):
        """A refusal when this principal started CONVERSATION_RESEARCH_LIMIT research tasks from conversation in
        the last CONVERSATION_RESEARCH_WINDOW seconds, else None. Caller holds _research_lock."""
        now = self.clock()
        rows = db.execute("SELECT created_at FROM web_tasks WHERE origin=? AND family IS ? AND created_at>? "
                          "ORDER BY created_at", (principal.origin, principal.family,
                                                  now - CONVERSATION_RESEARCH_WINDOW)).fetchall()
        if len(rows) < CONVERSATION_RESEARCH_LIMIT:
            return None
        wait = max(1, round((rows[-CONVERSATION_RESEARCH_LIMIT][0] + CONVERSATION_RESEARCH_WINDOW - now) / 60))
        minutes = int(CONVERSATION_RESEARCH_WINDOW // 60)
        return _say("rate_limited", f"I've started {len(rows)} web searches from our conversation in the last "
                                    f"{minutes} minutes, so I'm not starting another one now. Ask again in about "
                                    f"{wait} minute{'s' if wait != 1 else ''}, or use the research box.")

    # -- helpers -----------------------------------------------------------------------------------------------
    def _last_task(self, db, ctx, principal):
        """The conversation's last task, else the principal's latest research from any device (a Pocket device: its
        family's; the owner: any user-started task), so research started in Pocket or the console is reachable."""
        from ..webcortex import orchestrator
        tid = ctx.get("last_task_id")
        if not tid:
            if principal.owner:
                users = orchestrator.USER_ORIGINS
                row = db.execute("SELECT id FROM web_tasks WHERE origin IN (" + ",".join("?" * len(users)) +
                                 ") ORDER BY created_at DESC, rowid DESC LIMIT 1", users).fetchone()
            elif principal.family:
                row = db.execute("SELECT id FROM web_tasks WHERE family=? ORDER BY created_at DESC, rowid DESC "
                                 "LIMIT 1", (principal.family,)).fetchone()
            else:
                row = None
            tid = row[0] if row else None
        if not tid:
            return None
        try:
            return orchestrator.get(db, tid, family=principal.family)
        except (NotFound, Conflict, ValidationError):
            return None

    def _confirm(self, intent, action, args, summary):
        expires = self.clock() + CONFIRM_SECONDS
        pending = {"id": secrets.token_hex(8), "action": action, "args": args, "summary": summary,
                   "expires_at": expires}
        return Reply(intent, speech=[summary, "Say confirm, or press Confirm, to go ahead."],
                     text=summary + " (Say 'confirm' or press Confirm within 60 seconds.)", confirm=pending)

    def _refused_change(self, db, principal, route, task_id=None):
        """None when this principal may make this research change by voice, else the refusal. A Pocket device obeys
        the HTTP research route's rules: research_routes.gate for an unsigned request (a signed node's change needs
        its signature, which voice cannot carry) and, for a task's session, nodes.may_drive. A Pocket principal
        without its device digest is refused (fail closed); the owner console is not a Pocket device."""
        if principal.owner:
            return None
        device = principal.node_device
        if not device:
            return _say("refused", "I couldn't tell which device this is, so I didn't change any research.")
        from ..pocket import research_routes
        from ..webcortex import nodes
        now = self.clock()
        data = {"id": task_id} if task_id else {"objective": "-"}
        if research_routes.gate(db, principal.family, device, route, data, b"", {}, now) is not None:
            return _say("refused", NODE_SIGNED)
        if task_id and not nodes.may_drive(db, task_id, device, now):
            return _say("refused", SESSION_OWNED, task_id=task_id)
        return None

    def _owner_only(self, principal, what):
        if principal.owner:
            return None
        return _say("refused", f"{what} needs the owner console; this device is not allowed to do it.")

    # -- entry points --------------------------------------------------------------------------------------------
    def handle(self, db, principal: Principal, ctx: dict, text: str, pending: dict | None = None) -> Reply:
        intent = commands.parse(text)
        if intent.kind in ("confirm", "deny"):
            if not pending:
                return _say(intent.kind, "There is nothing waiting for confirmation.")
            if intent.kind == "deny":
                return Reply("deny", speech=["Okay, I won't do that."], text=f"Not done: {pending['summary']}",
                             actions=[{"kind": "clear_confirm"}])
            return self.execute(db, principal, ctx, pending)
        if intent.kind == "research":
            # "Is that version secure?" points back to the conversation's previous question, as in the console
            # (webcortex.intent.followup; T1-10): the subject comes only from that question, never a guess
            from ..webcortex import intent as wintent
            follow = wintent.followup(intent.arg, ctx.get("last_objective"))
            if follow and "clarify" in follow:
                return _say("clarify", follow["clarify"])
            return self._submit(db, principal, ctx, follow["question"] if follow else intent.arg,
                                instruction=intent.instruction)
        if intent.fuzzy and intent.kind in CONFIRM_IF_FUZZY:
            what = CONFIRM_IF_FUZZY[intent.kind] + (f" ({intent.arg.replace('_', ' ')})" if intent.arg else "")
            return self._confirm(intent.kind, "intent", {"kind": intent.kind, "arg": intent.arg},
                                 f"I heard \"{' '.join(text.split())[:80]}\". Did you mean: {what}?")
        handler = getattr(self, "_" + intent.kind)
        return handler(db, principal, ctx, intent.arg)

    def execute(self, db, principal, ctx, pending) -> Reply:
        """Run a confirmed sensitive action (the caller checked id, session and expiry)."""
        from ..webcortex import orchestrator
        action, args, now = pending["action"], pending["args"], self.clock()
        out = None
        if action == "disclose":
            out = self._submit(db, principal, ctx, args["objective"], authorization=orchestrator.AUTHORIZED_DISCLOSURE,
                               instruction=args.get("instruction"))
        elif action == "forget_research":
            from ..instance import governance
            governance.forget_research(db, args["task_id"], now)
            out = _say("forget", "I forgot that research, its sources and the knowledge that rested only on it.",
                       actions=[{"kind": "context", "last_task_id": None}])
        elif action == "forget_conversation":
            out = Reply("forget", speech=["I deleted this conversation's transcript."],
                        text="Transcript of this voice session deleted.",
                        actions=[{"kind": "forget_conversation"}])
        elif action == "enable_autonomy":
            out = self._switch_autonomy(db, True)
        elif action == "remember":
            from ..webcortex import knowledge
            done = 0
            for kid in args["knowledge_ids"]:
                try:
                    knowledge.review(db, kid, True, now, note="accepted by the owner by voice")
                    done += 1
                except InstanceError:
                    continue
            out = _say("remember", f"I accepted {done} conclusion{'s' if done != 1 else ''} into memory.")
        elif action == "intent" and args.get("kind") in CONFIRM_IF_FUZZY:   # a fuzzy match the owner confirmed
            out = getattr(self, "_" + args["kind"])(db, principal, ctx, args.get("arg"))
        elif action == "handoff":
            out = Reply("handoff", speech=["The microphone is now on this device."],
                        text="Microphone moved to this device.", actions=[{"kind": "handoff"}])
        if out is None:
            return _say("error", "That confirmation is not recognised.")
        out.actions.append({"kind": "clear_confirm"})
        return out

    # -- intents ------------------------------------------------------------------------------------------------
    def _submit(self, db, principal, ctx, objective, authorization=None, instruction=None):
        with self._research_lock:
            return self._submit_locked(db, principal, ctx, objective, authorization, instruction)

    def _submit_locked(self, db, principal, ctx, objective, authorization=None, instruction=None):
        from ..webcortex import orchestrator
        if not principal.research:
            return _say("refused", "Research from this device is switched off. Turn on Pocket research in the "
                                   "console to allow it.")
        refused = self._refused_change(db, principal, "research_submit")
        if refused is not None:
            return refused
        limited = self._research_limited(db, principal)
        if limited is not None:
            return limited

        # the instruction ("summarize their importance") is how to write the answer, not what to search for: it
        # reaches answer synthesis as a style instruction (task context), under the same evidence guard
        context = {"presentation": " ".join(instruction.split())[:200]} if instruction and instruction.strip() else None

        def submit():
            return orchestrator.submit(db, objective, self.clock(), origin=principal.origin, family=principal.family,
                                       authorization=authorization, context=context)
        try:
            task = principal.admit(submit) if principal.admit else submit()
        except ValidationError as err:
            if "private data" in str(err):
                if not principal.owner:
                    return _say("refused", "That question contains private data, so it was not sent anywhere.")
                kinds = str(err).split("(", 1)[-1].split(")", 1)[0]
                return self._confirm("research", "disclose", {"objective": objective, "instruction": instruction},
                                     f"That question contains private data ({kinds}). Send it to the web anyway?")
            return _say("refused", f"I couldn't start that research: {str(err)[:200]}")
        except Conflict as err:
            return _say("refused", f"Web research is not available: {str(err)[:160]}")
        except InstanceError as err:
            return _say("refused", f"I couldn't start that research: {str(err)[:200]}")
        if task is None:
            return _say("refused", "This device has reached its hourly research limit.")
        again = task.get("deduplicated")
        line = ("I'm already researching that." if again else "I'm researching that now. I'll tell you what I find.")
        text = f"{line} Task: {task['objective'][:300]}" + (f" (then: {instruction[:200]})" if instruction else "")
        return Reply("research", speech=[line], text=text, task_id=task["id"], follow=task["id"],
                     actions=[{"kind": "context", "last_task_id": task["id"], "last_objective": task["objective"][:300],
                               "last_instruction": instruction[:200] if instruction else None}])

    def _research(self, db, principal, ctx, objective):
        return self._submit(db, principal, ctx, objective)

    def _unclear(self, db, principal, ctx, _):
        return _say("unclear", "Ask me a question, or say help to hear what I can do.")

    def _chatter(self, db, principal, ctx, _):
        return _say("chatter", "That doesn't sound like a question, so I didn't start a web search. Ask me a "
                               "question, or say research followed by a topic.")

    def _help(self, db, principal, ctx, _):
        return _say("help", HELP)

    def _history(self, db, principal, ctx, _):
        """What this principal has been researching (R2-07): the conversation's last question and its own recent
        tasks (the owner: tasks a person asked for; a Pocket device: its family's), read from history, never searched."""
        from ..webcortex import orchestrator
        if principal.owner:
            rows = [r for r in db.execute("SELECT id, objective, status, origin FROM web_tasks ORDER BY created_at "
                                          "DESC, rowid DESC LIMIT 50") if orchestrator.user_initiated(r[3])]
        elif principal.family:
            rows = db.execute("SELECT id, objective, status, origin FROM web_tasks WHERE family=? ORDER BY "
                              "created_at DESC, rowid DESC LIMIT 5", (principal.family,)).fetchall()
        else:
            rows = []
        rows = rows[:HISTORY_ITEMS]
        last = str(ctx.get("last_objective") or "").strip()[:300]
        if not rows and not last:
            return _say("history", "You haven't asked me to research anything yet.")
        words = {"completed": "finished", "partial": "finished with a partial result", "failed": "failed",
                 "cancelled": "cancelled", "queued": "waiting to start"}
        state = (lambda st: words.get(st, "still in progress"))
        speech = []
        if last:
            speech.append(f"In this conversation you last asked about {last[:160]}.")
        if rows:
            head = rows[0]
            if not last or head[1][:300] != last:
                speech.append(f"Your most recent research is {head[1][:160]}: {state(head[2])}.")
            if len(rows) > 1:
                speech.append(f"{len(rows) - 1} earlier question{'s are' if len(rows) > 2 else ' is'} on screen.")
        lines = ([f"This conversation: {last}"] if last else []) + \
            (["Your recent research, newest first:"] + [f"- {o[:300]} ({state(st)})" for _, o, st, _ in rows] if rows else [])
        return Reply("history", speech=speech, text="\n".join(lines), task_id=rows[0][0] if rows else None)

    def _stop_speaking(self, db, principal, ctx, _):
        task = self._last_task(db, ctx, principal)
        running = task and task["status"] not in ("completed", "partial", "failed", "cancelled")
        text = "Stopped speaking." + (" The research continues; I'll show the result when it's ready."
                                      if running else "")
        return Reply("stop_speaking", speech=[], text=text, actions=[{"kind": "stop_speaking"}])

    def _resume_speaking(self, db, principal, ctx, _):
        return Reply("resume_speaking", speech=[], text="Continuing where I stopped.",
                     actions=[{"kind": "resume_speaking"}])

    def _cancel_research(self, db, principal, ctx, _):
        from ..webcortex import orchestrator
        task = self._last_task(db, ctx, principal)
        if not task or task["status"] in ("completed", "partial", "failed", "cancelled"):
            return _say("cancel_research", "There is no research running to cancel.")
        refused = self._refused_change(db, principal, "research_cancel", task["id"])
        if refused is not None:
            return refused
        orchestrator.cancel(db, task["id"], self.clock(), family=principal.family)
        return _say("cancel_research", f"Cancelled the research on {task['objective'][:120]}.", task_id=task["id"])

    def _show_evidence(self, db, principal, ctx, _):
        from ..webcortex import orchestrator
        task = self._last_task(db, ctx, principal)
        if not task:
            return _say("show_evidence", "There is no research in this conversation yet.")
        ev = orchestrator.evidence(db, task["id"], family=principal.family)
        n = len(ev.get("sources") or [])
        # number the sources as the answer cites them (the report's [n]); sources the report does not list follow
        cited = {s["url"]: s["n"] for s in _sources(task.get("result"))}
        numbered, extra = [], max(cited.values(), default=0)
        for s in ev.get("sources") or []:
            k = next((cited[u] for u in (s.get("canonical_url"), s.get("final_url"), s.get("url")) if u in cited), None)
            if k is None:
                extra += 1
                k = extra
            numbered.append((k, s))
        lines = [f"[{k}] {s.get('title') or s.get('url')} ({s.get('domain') or ''})"
                 for k, s in sorted(numbered, key=lambda x: x[0])[:20]]
        return Reply("show_evidence", speech=[f"I read {n} source{'s' if n != 1 else ''} for that. They're on "
                                              "screen."],
                     text=f"Evidence for: {task['objective'][:300]}\n" + "\n".join(lines), task_id=task["id"],
                     actions=[{"kind": "show_evidence", "task_id": task["id"]}])

    def _remember(self, db, principal, ctx, _):
        refused = self._owner_only(principal, "Accepting conclusions into memory")
        if refused:
            return refused
        task = self._last_task(db, ctx, principal)
        if not task:
            return _say("remember", "There are no conclusions in this conversation to remember yet.")
        ids = [r[0] for r in db.execute("SELECT id FROM web_knowledge WHERE task_id=? AND status='provisional' "
                                        "ORDER BY id", (task["id"],))]
        if not ids:
            return _say("remember", "That research produced no new provisional conclusions to accept.")
        return self._confirm("remember", "remember", {"knowledge_ids": ids},
                             f"Accept {len(ids)} conclusion{'s' if len(ids) != 1 else ''} from the research on "
                             f"{task['objective'][:100]} into memory?")

    def _learned_since(self, db, principal, ctx, arg):
        refused = self._owner_only(principal, "Reading what I have learned")   # the whole knowledge base
        if refused:
            return refused
        from ..webcortex import knowledge
        since = self.clock() - commands.SINCE.get(arg or "yesterday", 86400.0)
        new = knowledge.rows(db, "created_at>=? AND status IN ('provisional','accepted')", (since,))
        updated = [r for r in knowledge.rows(db, "updated_at>=? AND created_at<? AND status IN "
                                                 "('provisional','accepted','stale')", (since, since))]
        tasks = db.execute("SELECT count(*) FROM web_tasks WHERE finished_at>=? AND status IN ('completed','partial')",
                           (since,)).fetchone()[0]
        when = arg or "yesterday"
        if not new and not updated:
            return _say("learned_since", f"Since {when} I have not added or changed any knowledge. "
                                         f"{tasks} research task{'s' if tasks != 1 else ''} finished.")
        head = (f"Since {when} I added {len(new)} item{'s' if len(new) != 1 else ''} of knowledge and updated "
                f"{len(updated)}, from {tasks} finished research task{'s' if tasks != 1 else ''}.")
        lines = [f"- [{r['status']}] {r['statement'][:300]}" for r in (new + updated)[:15]]
        speech = [head]
        if new:
            speech.append(f"For example, {('provisionally: ' if new[0]['status'] == 'provisional' else '')}"
                          f"{new[0]['statement'][:200]}")
        return Reply("learned_since", speech=spoken(speech, 2, 80), text=head + "\n" + "\n".join(lines))

    def _simpler(self, db, principal, ctx, _):
        task = self._last_task(db, ctx, principal)
        report = task.get("result") if task and isinstance(task.get("result"), dict) else None
        if not report:
            return _say("simpler", "There is no answer in this conversation to explain more simply yet.")
        model = self._simplify_with_model(db, task, report)
        if model is not None:
            return model
        base = compose_answer(task)
        short = spoken(base.speech[:1], 1, 35) if base.speech else []
        return Reply("simpler", speech=short, text="\n".join(short) or base.text, source="template",
                     label="Shorter extract of the answer (no model available to simplify it)", task_id=task["id"],
                     sources=base.sources)

    def _simplify_with_model(self, db, task, report):
        """Ask the local model (inference gateway) for a simpler answer from the same evidence bundle, then apply
        the same evidence guard as research answers. None when no model or nothing passed."""
        from ..inference.gateway import InferenceError
        from ..webcortex import reasoning
        try:
            gw = reasoning.gateway_for(db, None, task["id"])
        except Exception:
            return None
        if gw is None:
            return None
        b = reasoning.bundle(report, task["objective"])
        if not b["claims"]:
            return None
        msgs = reasoning.messages(b)
        msgs[0] = {"role": "system", "content": reasoning.SYSTEM + "\nAdditionally: the owner asked for a SIMPLER "
                   "explanation. Use at most 3 short sentences, each restating one claim in fewer of its own words; "
                   "never drop a qualifier that limits a claim."}
        try:
            value, _ = gw.structured(msgs, reasoning.answer_schema(b), name="nsf_simple", purpose="voice_simplify",
                                     max_tokens=400, temperature=0.1)
        except InferenceError:
            return None
        accepted, _rejected = reasoning.guard(reasoning.compose(value, b), b)
        if not accepted:
            return None
        lines = [_cited(a) for a in accepted]
        cited = {n for a in accepted for n in a["citations"]}
        model = getattr(gw.cfg, "model", "") or "local model"
        return Reply("simpler", speech=spoken([a["text"] for a in accepted], 3, 70), text="\n".join(lines),
                     source="model", label=f"Simpler answer by {model}; every sentence passed the evidence guard",
                     task_id=task["id"], sources=_sources(report, cited))

    def _continue_research(self, db, principal, ctx, _):
        from ..webcortex import orchestrator
        task = self._last_task(db, ctx, principal)
        if not task:
            last = ctx.get("last_objective")
            if last:
                return self._submit(db, principal, ctx, last, instruction=ctx.get("last_instruction"))
            return _say("continue_research", "There is no previous investigation to continue.")
        if task["status"] in ("partial", "cancelled", "failed"):
            if not principal.research:
                return _say("refused", "Research from this device is switched off.")
            refused = self._refused_change(db, principal, "research_continue", task["id"])
            if refused is not None:
                return refused
            with self._research_lock:
                limited = self._research_limited(db, principal)
            if limited is not None:
                return limited

            def requeue():
                return orchestrator.requeue(db, task["id"], self.clock(), origin=principal.origin,
                                            family=principal.family)
            try:   # counted in the family's submissions per hour, as Pocket's research_continue is
                queued = principal.admit(requeue) if principal.admit else requeue()
            except InstanceError as err:
                return _say("refused", f"I couldn't continue it: {str(err)[:160]}")
            if queued is None:
                return _say("refused", "This device has reached its hourly research limit.")
            return Reply("continue_research", speech=["Continuing the previous investigation."],
                         text=f"Continuing: {task['objective'][:300]}", task_id=queued["id"], follow=queued["id"],
                         actions=[{"kind": "context", "last_task_id": queued["id"]}])
        if task["status"] == "completed":
            same = ctx.get("last_task_id") == task["id"]
            return self._submit(db, principal, ctx, task["objective"],
                                instruction=ctx.get("last_instruction") if same else None)
        return _say("continue_research", "That investigation is still running; I'll tell you when it's done.",
                    task_id=task["id"])

    def _mode(self, db, principal, ctx, mode):
        words = {"text_only": "Switched to text only. The microphone and speech are off.",
                 "voice_in_text_out": "I'll reply in text only. The microphone stays available.",
                 "voice_text": "I'll speak my answers again."}
        return Reply("mode", speech=[words[mode]] if mode == "voice_text" else [], text=words[mode],
                     actions=[{"kind": "mode", "mode": mode}])

    def _forget(self, db, principal, ctx, what):
        if what == "conversation":
            return self._confirm("forget", "forget_conversation", {}, "Delete this conversation's transcript?")
        refused = self._owner_only(principal, "Forgetting research")
        if refused:
            return refused
        task = self._last_task(db, ctx, principal)
        if not task:
            return _say("forget", "There is no research in this conversation to forget.")
        return self._confirm("forget", "forget_research", {"task_id": task["id"]},
                             f"Forget the research on {task['objective'][:100]}, its sources and the knowledge that "
                             "rests only on it?")

    def _enable_autonomy(self, db, principal, ctx, _):
        refused = self._owner_only(principal, "Enabling autonomous research")
        if refused:
            return refused
        return self._confirm("enable_autonomy", "enable_autonomy", {},
                             "Turn on autonomous research? It runs on its own within the daily budget.")

    def _disable_autonomy(self, db, principal, ctx, _):
        refused = self._owner_only(principal, "Changing autonomous research")
        if refused:
            return refused
        return self._switch_autonomy(db, False)

    def _switch_autonomy(self, db, on):
        """The owner's switch through autonomy.enable/disable, so it is recorded (by owner:voice) like the console's;
        the reply says what the switch actually is afterwards."""
        from ..webcortex import autonomy
        intent = "enable_autonomy" if on else "disable_autonomy"
        try:
            (autonomy.enable if on else autonomy.disable)(db, self.clock(), by="owner:voice")
        except InstanceError as err:
            return _say(intent, f"I couldn't change autonomous research: {str(err)[:160]}")
        if autonomy.enabled(db) != on:
            return _say(intent, "I couldn't change autonomous research; it is still " + ("off." if on else "on."))
        return _say(intent, "Autonomous research is now on, within its daily budget." if on
                    else "Autonomous research is off.")

    def _handoff(self, db, principal, ctx, _):
        return self._confirm("handoff", "handoff", {}, "Move the microphone to this device? The other device "
                                                        "stops listening.")
