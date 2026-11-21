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
  research                       "research X", "look up X", "investigate X", "find out X" (arg X), or any other
                                 utterance of at least MIN_QUESTION_WORDS words (the question path text uses)
  unclear                        anything shorter (greetings, fragments)
"""
from __future__ import annotations

from dataclasses import dataclass
import re

MIN_QUESTION_WORDS = 3


@dataclass(frozen=True)
class Intent:
    kind: str
    arg: str | None = None


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
_RESEARCH = re.compile(r"^(?:please )?(?:research|look up|investigate|find out(?: about)?|search for|"
                       r"tell me about|what do you know about|check)\s+(.+)$")


def parse(text: str) -> Intent:
    norm = normalise(text or "")
    if not norm:
        return Intent("unclear")
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
    m = _RESEARCH.match(norm)
    original = " ".join((text or "").split())
    if m:
        # keep the owner's own wording (case, punctuation) for the objective: drop the command words only
        words = len(norm.split()) - len(m.group(1).split())
        return Intent("research", " ".join(original.split()[words:]) or m.group(1))
    if len(norm.split()) >= MIN_QUESTION_WORDS:
        return Intent("research", original)
    return Intent("unclear")


SINCE = {"yesterday": 86400.0, "today": 86400.0, "the last day": 86400.0, "this week": 7 * 86400.0,
         "the last week": 7 * 86400.0, "recently": 7 * 86400.0}
