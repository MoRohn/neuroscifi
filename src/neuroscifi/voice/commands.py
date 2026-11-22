"""Deterministic parsing of a final utterance into a voice intent (no model decides what a command means).

parse(text) -> Intent(kind, arg). Kinds:
  confirm / deny                 answer a pending confirmation ("confirm", "yes confirm" / "cancel that", "no")
  stop_speaking                  "stop", "stop talking", "be quiet", "stop speaking but keep researching"
  resume_speaking                "go on", "continue speaking", "keep talking"
  cancel_research                "cancel the research", "stop researching"
  show_evidence                  "show (me) the evidence/sources"
  remember                       "remember (these|the) conclusions", "remember that"
  learned_since                  "what have you learned since yesterday|today|this week"
  simpler                        "explain (that) more simply", "simpler please"
  continue_research              "continue (the) previous investigation", "keep researching that"
  mode                           "switch to text only" (text_only), "text replies only"/"stop talking out loud"
                                 (voice_in_text_out), "speak your answers"/"switch to voice" (voice_text)
  forget                         "forget this conversation" (arg 'conversation'), "forget the last research"
                                 (arg 'research')
  enable_autonomy                "enable autonomous research", "turn on autonomy"
  disable_autonomy               "disable/turn off autonomous research"
  handoff                        "use this device for the microphone", "move the microphone here"
  help                           "help", "what can you do"
  history                        "what was I researching", "what did I ask earlier", "show my recent research": the
                                 conversation's and the principal's own research history, never a web search (R2-07)
  research                       "research X", "look up X", "investigate X", "find out X" (arg X), or another
                                 utterance of at least MIN_QUESTION_WORDS words that asks for something: it ends
                                 with "?" or starts with a question word or an information request (_ASKS)
  chatter                        other sentences (remarks, thanks, statements): no web search is started (UAT T5:
                                 every sentence of conversation became a web research query)
  unclear                        anything shorter (greetings, fragments)

Robustness (0.7.0, docs/voice/VOICE_VALIDATION.md): the literal grammar above is tried first. Then the utterance is
canonicalised (fillers, politeness and "for a second"-style tails dropped, repeated words collapsed) and matched
against the same grammar and a synonym set ("hold on", "hang on", "continue", "where did you get that", ...). Last,
a short utterance (at most FUZZY_MAX_WORDS words) that is close in spelling to one command phrase - and clearly
closer to it than to any phrase of another command - is taken as that command with fuzzy=True (FUZZY_MIN ratio,
FUZZY_MARGIN margin). "confirm" is never matched fuzzily, and the bridge asks before it runs a fuzzy match of a
command with an effect beyond speech (bridge.CONFIRM_IF_FUZZY); the sensitive commands ask in any case.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re

MIN_QUESTION_WORDS = 3
FUZZY_MAX_WORDS = 6
FUZZY_MIN = 0.84          # similarity (difflib ratio over characters) needed for a fuzzy command match
FUZZY_MARGIN = 0.08       # ... and this much more similar than the best phrase of any other command
CLAUSE_MAX_WORDS = 12     # longer utterances are not split into clauses to look for a command


@dataclass(frozen=True)
class Intent:
    kind: str
    arg: str | None = None
    instruction: str | None = None     # research only: what to do with the findings ("summarize their importance")
    fuzzy: bool = False                # matched by spelling similarity, not by the grammar (see FUZZY_MIN)


# The address ("NeuroSciFi, ..." and how speech recognition hears it: "New Roe Sci-Fi", "neuro sci fi") is not part
# of the request; an instruction after the subject ("... and summarize their importance") is not the research subject.
_WAKE = re.compile(r"^\W*(?:(?:hey|ok|okay|hi|hello)\W+)?(?:neuro|new\W*r(?:o|oe|ow))\W*sci\W*fi\b[\s,.!:;-]*",
                   re.IGNORECASE)
_INSTRUCTION = re.compile(r"[\s,;]+(?:and|then|,)\s+(?:then\s+)?(?:please\s+)?((?:summari[sz]e|explain|describe|"
                          r"tell me|list|outline|compare|give me|rank)\b.*?)[\s.!?]*$", re.IGNORECASE)


def _split_instruction(objective: str):
    m = _INSTRUCTION.search(objective)
    if m and len(objective[:m.start()].split()) >= 2:
        return objective[:m.start()].rstrip(" ,;"), m.group(1)
    return objective, None


def normalise(text: str) -> str:
    text = text.lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return " ".join(text.split())


_PATTERNS = [
    ("confirm", r"(?:yes )?(?:i )?confirm(?:ed)?(?: (?:it|that|please))?|yes please confirm"),
    ("deny", r"(?:no|nope|cancel that|don't|do not)(?: (?:do it|do that|thanks|thank you))?"),
    ("stop_speaking", r"(?:please )?(?:stop|stop (?:talking|speaking)(?: (?:but|and) keep (?:researching|working|going)"
                      r"(?: on it)?)?|be quiet|quiet|silence|shush|hush|that's enough)(?: please)?"),
    ("resume_speaking", r"(?:please )?(?:go on|continue speaking|keep talking|carry on|continue reading|"
                        r"continue where you (?:left off|stopped))(?: please)?"),
    ("cancel_research", r"(?:please )?(?:cancel|stop|abort) (?:the |this |that )?(?:research|investigation|search|"
                        r"researching|investigating)(?: please)?"),
    ("show_evidence", r"(?:please )?(?:show|display|list)(?: me)? (?:the |your )?(?:evidence|sources|citations)"
                      r"(?: please)?"),
    ("remember", r"(?:please )?remember (?:the |these |those |your |this |that )?(?:conclusions?|findings|that|this|"
                 r"it)(?: please)?"),
    ("simpler", r"(?:please )?(?:explain (?:that |it |this )?(?:more )?simpl(?:y|er)|simpler(?: please)?|"
                r"say (?:that|it) (?:more )?simply|in simpler (?:words|terms))(?: please)?"),
    ("continue_research", r"(?:please )?(?:continue|resume|keep going with) (?:the |my |our |that |this )?"
                          r"(?:previous |last )?(?:investigation|research)(?: please)?|keep researching(?: that| it)?"),
    ("forget_conversation", r"(?:please )?(?:forget|delete|erase) (?:this|the|our) (?:conversation|transcript)"),
    ("forget_research", r"(?:please )?(?:forget|delete|erase) (?:the |that |this )?(?:last |previous )?"
                        r"(?:research|investigation)"),
    ("enable_autonomy", r"(?:please )?(?:enable|turn on|switch on|start) (?:the )?(?:autonomous research|"
                        r"autonomy|autonomous mode)"),
    ("disable_autonomy", r"(?:please )?(?:disable|turn off|switch off|stop) (?:the )?(?:autonomous research|"
                         r"autonomy|autonomous mode)"),
    ("handoff", r"(?:please )?(?:use this (?:device|phone|computer) for (?:the )?(?:microphone|mic)|"
                r"move (?:the )?(?:microphone|mic) (?:here|to this device)|switch (?:the )?(?:microphone|mic) "
                r"to this device)"),
    ("help", r"help|what can you do|what can i say|list (?:the )?commands"),
    ("history", r"(?:please )?(?:what (?:was|were|am|are|have) (?:i|we) (?:been )?(?:researching|investigating|"
                r"looking (?:up|into|at)|asking(?: you)?(?: about)?|working on|talking about)|what did (?:i|we) "
                r"(?:research|investigate|ask(?: you)?(?: about)?|look up|look into|talk about)|what (?:was|is) (?:my|our) "
                r"(?:last|previous|latest|most recent) (?:question|research|investigation)|(?:show|list|tell)(?: me)? "
                r"(?:my|our) (?:recent |previous |last )?(?:research|questions|investigations)(?: history)?|"
                r"(?:my |our )?research history)(?: (?:earlier|before|today|yesterday|recently|lately|last time|so far))?"
                r"(?: please)?"),
]
_COMPILED = [(kind, re.compile(f"^(?:{p})$")) for kind, p in _PATTERNS]
_MODE = [
    ("text_only", re.compile(r"^(?:please )?(?:switch|change|go) to text(?: only)?(?: mode)?(?: please)?$|"
                             r"^text only(?: mode)?(?: please)?$")),
    ("voice_in_text_out", re.compile(r"^(?:please )?(?:text (?:replies|answers) only|stop talking out loud|"
                                     r"(?:reply|answer) in text|mute (?:your )?(?:voice|speech))(?: please)?$")),
    ("voice_text", re.compile(r"^(?:please )?(?:speak your (?:answers|replies)|switch (?:back )?to voice(?: mode)?|"
                              r"talk to me|unmute (?:your )?(?:voice|speech))(?: please)?$")),
]
_LEARNED = re.compile(r"^what (?:have you|did you) learn(?:ed|t)? (?:since |over |in )?(yesterday|today|this week|"
                      r"the last day|the last week|recently)$")
_ASKS = re.compile(r"^(?:what|what's|whats|who|who's|whom|whose|when|where|which|why|how|is|are|was|were|do|does|"
                   r"did|can|could|should|would|will|has|have|had|may|might|explain|compare|summari[sz]e|describe|"
                   r"define|list|give me|show me|tell me|find|check|verify|whether)\b")
_RESEARCH = re.compile(r"^(?:please )?(?:research|look up|investigate|find out(?: about)?|search for|"
                       r"tell me about|what do you know about|check)\s+(.+)$")


# Words around a command that do not change it ("um, okay, could you stop for a second please").
_LEAD = re.compile(r"^(?:(?:um+|uh+|uhm|er+m?|ah|oh|hmm+|mm+|okay|ok|alright|all right|right|well|so|hey|yeah|"
                   r"now|just|please|sorry|actually|like|can you|could you|would you|will you|can we|could we|"
                   r"would you mind|i said|i want you to|i'd like you to|you can|you could|you may|let's|"
                   r"i'm ready|go ahead and) )+")
_TAIL = re.compile(r"(?: (?:please|thanks|thank you|now|right now|then|okay|ok|for me|for a (?:second|sec|moment|"
                   r"minute|bit)|a (?:second|sec|moment|minute|bit)|already|again|there))+$")

# Synonyms (canonical form, exact): phrases people use for a command that the literal grammar does not cover.
SYNONYMS = {
    "stop_speaking": ["hold on", "hang on", "wait", "pause", "enough", "stop it", "stop that", "shut up",
                      "be silent", "stop reading", "no stop", "stop the speech", "stop playback", "stop the audio",
                      "quiet down", "that is enough", "enough of that", "that'll do", "stop there", "okay stop"],
    "resume_speaking": ["continue", "keep going", "go ahead", "resume", "proceed", "keep reading", "go on then",
                        "continue talking", "carry on talking", "go on talking", "resume speaking", "resume talking",
                        "unpause", "continue please", "carry on please", "finish what you were saying"],
    "show_evidence": ["what are your sources", "what is your source", "what's your source", "what's the source",
                      "where did you get that", "where did you get that from", "where is that from",
                      "where does that come from", "can i see the evidence", "can i see the sources",
                      "let me see the sources", "give me the sources", "give me the evidence", "cite your sources",
                      "sources", "evidence", "show sources", "show evidence", "what's your evidence",
                      "what is your evidence", "how do you know that", "how do you know"],
    "simpler": ["simplify that", "simplify it", "simplify", "make it simpler", "make that simpler",
                "say that in simpler words", "say it in simpler words", "in plain english", "plain english",
                "explain it like i'm five", "dumb it down", "explain it more simply", "explain that simply",
                "say that more simply", "i don't understand"],
    "cancel_research": ["drop the research", "never mind the research", "kill the research", "end the research",
                        "halt the research", "cancel the search", "stop the search", "stop searching"],
    "help": ["what commands are there", "what commands can i use", "what can i ask", "what do you do",
             "help me", "commands", "what are the commands", "what can you do for me"],
    "history": ["remind me what i was researching", "remind me what i asked", "what have i asked you",
                "what have i asked", "what did i ask you", "what was i asking you", "my research history",
                "research history", "what did we research", "what was i working on"],
    "continue_research": ["carry on researching", "keep investigating", "continue researching",
                          "continue the investigation", "continue investigating"],
    "deny": ["never mind", "no thanks", "no thank you", "cancel", "no don't", "don't", "nah"],
}
_SYNONYM = {phrase: kind for kind, phrases in SYNONYMS.items() for phrase in phrases}


# Command frames over the whole canonical utterance: a command verb with what it acts on (speech, the answer, the
# research...), optionally followed by a short remark ("cut the audio, I'll read it myself").
_SPEECH = r"(?:talking|speaking|reading(?: that)?(?: out)?|the speech|the audio|the voice|your voice|the playback|" \
          r"playback|the sound|it)"
_FRAMES = [
    ("stop_speaking", r"(?:stop|pause|cut|kill|mute|end|quit) " + _SPEECH + r"(?: (?:there|out loud|for now|aloud))?"
                      r"(?: i'll read it(?: myself)?| i can read it(?: myself)?)?|(?:that's|that is) (?:plenty|enough)"
                      r"(?: (?:quiet|stop|you can stop " + _SPEECH + r"))?|(?:be|go|keep|stay) (?:quiet|silent)"),
    ("resume_speaking", r"(?:continue|keep|carry on|go on|start|resume|pick up) (?:talking|speaking|reading)"
                        r"(?: (?:again|where you (?:left off|stopped)|with the answer))?|(?:keep going|go on|carry on|"
                        r"continue) with the answer|pick it (?:back )?up(?: again)?|(?:start|begin) (?:talking|"
                        r"speaking|reading) again"),
    ("show_evidence", r"(?:show|give|list|tell)(?: me)? (?:the |your )?(?:sources|evidence|references|citations|proof)"
                      r"(?: (?:for|behind|of) (?:that|this|it|your (?:last )?answer|the (?:last )?answer))?"
                      r"(?: you (?:used|relied on))?|(?:what|which) (?:are |were )?(?:your |the )?(?:sources|"
                      r"references|sites|citations)(?: did you use)?(?: (?:for|behind) (?:that|this|it|your "
                      r"(?:last )?answer|the (?:last )?answer))|what backs (?:that|this|it) up|can i see what backs "
                      r"(?:that|this|it) up"),
    ("simpler", r"(?:explain|say|put) (?:that|it|this)(?: again)? (?:in )?(?:simpler|plainer|easier) (?:terms|words|"
                r"language)|(?:explain|say) (?:that|it|this) (?:more )?(?:simply|plainly|easily)|make (?:that|it|"
                r"this) (?:simpler|easier)|what does (?:that|it|this) mean in plain english|explain (?:that |it )?"
                r"like i'm (?:five|ten|twelve|a child|a kid)"),
    ("cancel_research", r"(?:cancel|abort|kill|scrap|drop|stop|end|halt) (?:the |this |that |your |my )?(?:current |"
                        r"running )?(?:research|investigation|search)(?: task)?(?: (?:you're running|that's running|"
                        r"now))?"),
    ("help", r"(?:what|which) commands (?:do you (?:understand|know|accept)|can i (?:use|say)|are there)|"
             r"what are (?:my|the) options(?: here)?|how do i use (?:this|you)(?: thing)?|list (?:the )?(?:things|"
             r"commands) i can say(?: to you)?"),
    ("continue_research", r"(?:continue|resume|pick up|keep) (?:the |my |our |your )?(?:research|investigation|"
                          r"digging|researching)(?: (?:where you left off|you were doing(?: before)?|from before))?"),
]
_FRAME_RES = [(kind, re.compile(f"^(?:{p})$")) for kind, p in _FRAMES]
_DENY_LEAD = re.compile(r"^(?:no|nope|nah|negative)(?: (?:don't|do not)(?: do (?:that|it))?| i (?:changed my mind|"
                        r"don't want (?:that|to))| leave it(?: as it is)?| not now| i'm not sure(?: about that)?)*$")


def _clauses(text: str) -> list[str]:
    return [c for c in (canonical(normalise(part)) for part in re.split(r"[,;.!?]+|\s+-\s+", text)) if c]


def _frame(canon: str):
    for kind, rx in _FRAME_RES:
        if rx.match(canon):
            return Intent(kind)
    if _DENY_LEAD.match(canon) or canon in ("actually no", "no actually"):
        return Intent("deny")
    return None


def _one(canon: str):
    """A command from one canonical clause by grammar, synonym or frame (never 'confirm' here)."""
    hit = _grammar(canon)
    if hit and hit.kind != "confirm":
        return hit
    if canon in _SYNONYM:
        return _intent_of(_SYNONYM[canon])
    return _frame(canon)


def canonical(norm: str) -> str:
    """The command words of a normalised utterance: lead/tail filler dropped, immediate repeats collapsed (but
    "no no no" stays as it is: repeated refusals are conversation, not an answer to a confirmation)."""
    t = _TAIL.sub("", _LEAD.sub("", norm)).strip()
    t = t.replace("that's", "that is") if t not in _SYNONYM and t.replace("that's", "that is") in _SYNONYM else t
    out = []
    for w in t.split():
        if not out or out[-1] != w or w in ("no", "nope", "nah"):
            out.append(w)
    return " ".join(out)


def _grammar(norm: str):
    for kind, pattern in _COMPILED:
        if pattern.match(norm):
            if kind == "forget_conversation":
                return Intent("forget", "conversation")
            if kind == "forget_research":
                return Intent("forget", "research")
            return Intent(kind)
    for mode, pattern in _MODE:
        if pattern.match(norm):
            return Intent("mode", mode)
    m = _LEARNED.match(norm)
    if m:
        return Intent("learned_since", m.group(1))
    return None


# The phrase inventory for fuzzy matching: every synonym plus literal examples of the grammar.
_EXAMPLES = {
    "stop_speaking": ["stop", "stop talking", "stop speaking", "be quiet", "quiet", "silence", "shush", "hush",
                      "that's enough", "stop talking but keep researching", "stop speaking but keep researching"],
    "resume_speaking": ["go on", "continue speaking", "keep talking", "carry on", "continue reading",
                        "continue where you left off", "continue where you stopped"],
    "cancel_research": ["cancel the research", "stop researching", "cancel the investigation", "abort the research"],
    "show_evidence": ["show me the evidence", "show the evidence", "show me the sources", "show your sources",
                      "show the citations"],
    "remember": ["remember the conclusions", "remember these conclusions", "remember the findings"],
    "simpler": ["explain that more simply", "simpler please", "in simpler words", "in simpler terms"],
    "continue_research": ["continue the previous investigation", "keep researching that", "resume the research"],
    "forget:conversation": ["forget this conversation", "delete this conversation"],
    "forget:research": ["forget the last research", "delete the last research"],
    "enable_autonomy": ["enable autonomous research", "turn on autonomy"],
    "disable_autonomy": ["disable autonomous research", "turn off autonomy"],
    "handoff": ["use this device for the microphone", "move the microphone here"],
    "help": ["help", "what can you do", "what can i say", "list the commands"],
    "history": ["what was i researching", "what did i ask earlier", "show my recent research",
                "what were we talking about", "what was my last question"],
    "mode:text_only": ["switch to text only", "text only"],
    "mode:voice_in_text_out": ["text replies only", "stop talking out loud", "reply in text"],
    "mode:voice_text": ["speak your answers", "switch to voice"],
    "deny": ["no", "cancel that", "do not do that"],
}
_FUZZY = [(phrase, label) for label, phrases in _EXAMPLES.items() for phrase in phrases] + \
    [(phrase, kind) for phrase, kind in _SYNONYM.items() if len(phrase) >= 4]


def _intent_of(label: str, fuzzy=False) -> Intent:
    kind, _, arg = label.partition(":")
    return Intent(kind, arg or None, fuzzy=fuzzy)


def _fuzzy(canon: str):
    if not canon or len(canon.split()) > FUZZY_MAX_WORDS or len(canon) < 4:
        return None
    best = {}
    for phrase, label in _FUZZY:
        ratio = SequenceMatcher(None, canon, phrase).ratio()
        if ratio > best.get(label, 0.0):
            best[label] = ratio
    ranked = sorted(best.items(), key=lambda kv: -kv[1])
    label, top = ranked[0]
    runner = ranked[1][1] if len(ranked) > 1 else 0.0
    if top >= FUZZY_MIN and top - runner >= FUZZY_MARGIN:
        return _intent_of(label, fuzzy=True)
    return None


def parse(text: str) -> Intent:
    text = _WAKE.sub("", text or "", count=1)
    norm = normalise(text)
    if not norm:
        return Intent("unclear")
    hit = _grammar(norm)
    if hit:
        return hit
    canon = canonical(norm)
    hit = _one(canon) if canon else None         # "confirm" only in the literal grammar's own words
    if hit:
        return hit
    clauses = _clauses(text) if len(norm.split()) <= CLAUSE_MAX_WORDS else []
    if len(clauses) > 1:   # "hold on, stop reading that out": one clause is a command and no clause asks something
        # never "no" from inside a longer sentence ("No, I can't remember."): a refusal is the whole utterance
        found = {(i.kind, i.arg) for i in map(_one, clauses) if i and i.kind != "deny"}
        asks = any(_ASKS.match(c) or _RESEARCH.match(c) for c in clauses if not _one(c))
        if len(found) == 1 and not asks:
            kind, arg = found.pop()
            return Intent(kind, arg)
    m = _RESEARCH.match(norm)
    original = " ".join((text or "").split())
    if m:
        # keep the owner's own wording (case, punctuation) for the objective: drop the command words only
        words = len(norm.split()) - len(m.group(1).split())
        objective, instruction = _split_instruction(" ".join(original.split()[words:]) or m.group(1))
        return Intent("research", objective, instruction)
    hit = _fuzzy(canon)
    if hit:
        return hit
    if len(norm.split()) >= MIN_QUESTION_WORDS:
        if not (original.rstrip().endswith("?") or _ASKS.match(norm)):
            return Intent("chatter")
        objective, instruction = _split_instruction(original)
        return Intent("research", objective, instruction)
    return Intent("unclear")


SINCE = {"yesterday": 86400.0, "today": 86400.0, "the last day": 86400.0, "this week": 7 * 86400.0,
         "the last week": 7 * 86400.0, "recently": 7 * 86400.0}
