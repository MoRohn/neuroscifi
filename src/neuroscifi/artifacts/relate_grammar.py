"""A closed grammar for the questions the relater answers (lane FX5, ADR-081: construction C1).

Two rounds of word rules let the relater answer questions it did not read: a qualifier ("apart from X", one named
member, compressed sizes, another unit, another digest, loudness, notes) was ignored and the comparison answered for
everything it measures. This module replaces "a cue word is present" by "every word is accounted for": a question
decides a comparison only when it is a yes/no question and EVERY one of its words is consumed by a small, documented
vocabulary of that comparison (its quantity, where it is read from, scope words meaning "all of it", positive words
of judgement and of stating) or by a fixed list of function words, or is the name of one of the two files. Any other
word (a negation, a restriction, a named member, a unit, a number, a word this grammar does not know) leaves the
question unread, and the comparison answers "cannot", naming the words it did not read.

The vocabularies are general English words for each quantity and structure, written here from the meaning of each
comparison, never from a benchmark case. They are deliberately small: a question outside them is "cannot", which is
the intended cost (a wrong answer is far worse than "cannot"). Words that reverse or narrow a question (not, no,
only, except, any, wrong, one, first...) are in NO vocabulary, so they can never be consumed.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .relate_read import AGGREGATE_WORDS, singular, unit_of

# Words that carry no meaning a comparison could ignore wrongly: articles, auxiliaries, plain prepositions, pronouns.
# Deliberately absent: not no nor any some only just one other except apart besides but or if unless than more less
# most least how why where who whom when (times only) before after first last then also without.
FUNCTION = frozenset("""a an the of in on at to for from by with into onto against about as is are was were be been
being do does did has have had it its this that these those they them their there here what which whose and ones""".split())
# A yes/no question begins with one of these (an open question, an imperative, a fragment such as "Cost?" is not one).
FRAME = frozenset("is are do does did was were has have".split())
# Words that narrow, reverse or condition a question: consumed by no path, whatever a file or a column is called.
NEVER = frozenset("""not no never none nor neither only just one except excepting excluding apart besides other but
or if unless without any some first last wrong incorrect incorrectly false differ differs different mismatch
mismatched disagree disagrees inconsistent missing""".split())
# Positive words of judgement only: "wrong", "incorrect", "false", "differ" reverse the answer and are never consumed.
JUDGE = frozenset("""right correct correctly match matches matched matching agree agrees agreeing agreement same
consistent accurate accurately true exact exactly really actually indeed""".split())
# Verbs with which an artifact states or shows something ("the sizes the list gives").
STATING = frozenset("""say says said state states stated give gives given list lists listed show shows shown showing
name names named record records recorded claim claims claimed contain contains hold holds write writes written
mention mentions mentioned""".split())
# Scope words that mean "all of it": a structure comparison always compares every line, so these narrow nothing.
ALL = frozenset("all every each".split())

_LISTING = frozenset("""archive archives zip zipfile bundle bundles package packages tarball tar folder member members
file files entry entries listing listings list lists manifest manifests inventory catalogue catalog packing text
document sheet note notes""".split())


@dataclass(frozen=True)
class Grammar:
    """One comparison's vocabulary: the words naming its quantity, the elements it compares one by one (its
    structure), and where it is read from. A question decides it only when it names all three, and names the
    elements in the plural or with "all", "every" or "each" (one member, one link, one beep is not what it compares;
    "the list's size" is the size of the list)."""
    name: str
    quantity: frozenset
    structure: frozenset
    where: frozenset
    extra: frozenset = frozenset()          # further words of this comparison's subject (a sound's events)


_SOUND_EVENTS = frozenset("""tone tones beep beeps chime chimes bell bells gong gongs click clicks pulse pulses sound
sounds""".split())
_LINKS = frozenset("link links linked linking connection connections connected connect connects edge edges join joins "
                   "joined joining".split())
GRAMMARS = {
    # an archive's member sizes against a listing (bytes are the only unit read)
    "size": Grammar("size", frozenset("size sizes sized byte bytes".split()),
                    frozenset("file files member members entry entries".split()), _LISTING),
    # an archive's member digests against a listing (an algorithm named must be the listing's)
    "digest": Grammar("digest", frozenset("checksum checksums hash hashes hashed digest digests md5 sha1 sha256 "
                                          "sha512".split()),
                      frozenset("file files member members entry entries".split()), _LISTING),
    # the pairs of labels a drawing joins against the pairs a text names (direction is not read)
    "link": Grammar("link", _LINKS, _LINKS,
                    frozenset("""diagram diagrams drawing drawings chart charts graph graphs figure figures picture svg
text texts list lists listing memo memos note notes document file files page sheet box boxes node nodes label
labels""".split()), frozenset("draw draws drawn".split())),
    # a sound's tone onsets against elapsed times written one per line
    "time": Grammar("time", frozenset("time times timing timings onset onsets start starts started starting begin "
                                      "begins began timestamp timestamps when".split()),
                    _SOUND_EVENTS | frozenset("cue cues onset onsets".split()),
                    frozenset("""recording recordings audio sound wav clip track file files cue cues sheet list
listing text document""".split()),
                    frozenset("ring rings rang play plays played happen happens happened occur occurs occurred "
                              "heard".split())),
}
# The plural nouns of the grammars (a verb ending in -s, "starts", "connects", is not one).
PLURALS = frozenset("""sizes bytes checksums hashes digests files members entries links connections edges times timings
onsets timestamps cues tones beeps chimes bells gongs clicks pulses sounds""".split())
ALGORITHMS = ("md5", "sha1", "sha256", "sha512")

# The older comparisons' general vocabulary (C1 applied to `specific`): aggregates, counts, measures and the
# structures they read. A question decided by an older comparison is `specific` only when every word is one of these,
# a function, judgement or stating word, an artifact or file-name word, a unit, or a word of the compared facts.
_LEGACY = frozenset(w for words in AGGREGATE_WORDS.values() for w in words) | frozenset("""number numbers count counts
many how width height depth length size sizes dimension dimensions extent extents wide tall high deep long lasts last
lasting duration bounding box boxes pixel pixels resolution frame frames area volume value values point points data
plotted plot line lines bar bars reading readings series order ordered sequence event events step steps timeline
chronology call calls called calling trace traces range ranges minimum maximum lowest highest stop stops row rows
column columns record records entry entries item items""".split())
_LEGACY_WHERE = frozenset("""image images picture pictures photo photos photograph photographs caption captions figure
figures file files document documents report reports text texts note notes label labels sound sounds recording
recordings audio clip clips table tables spreadsheet spreadsheets csv mesh meshes model models log logs code readme
screenshot screenshots transcript transcripts description descriptions title titles page pages drawing drawings svg
png wav archive archives zip bundle member members diagram diagrams chart charts graph graphs plot plots sheet sheets
list lists listing listings manifest manifests module modules source sources script scripts program programs route
routes track tracks gpx cue cues workbook memo memos summary newsletter letter letters email emails message
messages minutes invoice invoices receipt receipts article articles paper papers post posts notice notices manual
manuals guide guides brochure leaflet flyer poster statement statements""".split())


@dataclass
class Parse:
    """What the grammar read of a question."""
    frame: bool = False                     # a yes/no question
    unread: tuple = ()                      # words no vocabulary consumed (shown in the reason)
    quantity: set = field(default_factory=set)      # quantity words named
    where: bool = False                     # a word naming where (or a file's own name)
    algorithm: str | None = None            # a digest algorithm the question names
    bytes_named: bool = False
    structure: bool = True                  # the elements compared are named (structure grammars)
    many: bool = True                       # ... in the plural, or with all / every / each

    @property
    def complete(self) -> bool:
        return self.frame and not self.unread and bool(self.quantity) and self.where and self.structure and self.many


def tokens(question: str) -> list[str]:
    """Lower-case words of a question, keeping a path or a file name in one piece ("docs/guide.txt"), a possessive
    "'s" dropped; "SHA-256", "sha 256" folded to "sha256"."""
    q = unicodedata.normalize("NFKC", question or "")[:2000].casefold().replace("’", "'")
    q = re.sub(r"\bsha[\s_-]?(1|256|512)\b", r"sha\1", q)
    q = re.sub(r"\bmd[\s_-]?5\b", "md5", q)
    out = []
    for t in re.findall(r"[^\W_]+(?:['./_-][^\W_]+)*", q):
        if t.endswith("'s"):
            t = t[:-2]
        out.append(t)
    return out


def name_tokens(*names) -> set:
    """A question may name one of the two files by its whole name ("manifest.txt", "SHA256SUMS"); never by a part
    of it (a file's name is the artifact's own word: "only.txt" must not let "only" be read)."""
    out = set()
    for name in names:
        toks = tokens(str(name or ""))
        if len(toks) == 1 and toks[0] not in NEVER:
            out.add(toks[0])
    return out


def _consumed(word: str, vocab: frozenset) -> bool:
    if word in vocab:
        return True
    if "-" in word and "/" not in word and "." not in word:
        parts = word.split("-")
        return all(p in vocab for p in parts if p)
    return False


def parse(question: str, grammar: Grammar, names=()) -> Parse:
    """Read a question against one comparison's grammar."""
    toks = tokens(question)
    out = Parse(frame=bool(toks) and toks[0] in FRAME, structure=False, many=False)
    own = name_tokens(*names)
    vocab = FUNCTION | FRAME | JUDGE | STATING | ALL | grammar.quantity | grammar.structure | grammar.where | \
        grammar.extra
    unread = []
    for t in toks:
        if t in NEVER or (not _consumed(t, vocab) and t not in own):
            unread.append(t)
            continue
        if t in own and t not in vocab:
            out.where = True
            continue
        if t in grammar.quantity:
            out.quantity.add(t)
        if t in grammar.structure:
            out.structure = True
        if t in ALL or (t in PLURALS and (t in grammar.structure or t in grammar.quantity)):
            out.many = True
        if t in grammar.where:
            out.where = True
        if t in ALGORITHMS:
            out.algorithm = t
        if t in ("byte", "bytes"):
            out.bytes_named = True
    out.unread = tuple(dict.fromkeys(unread))
    return out


def algorithm_in_name(name: str | None) -> str | None:
    """The one digest algorithm a file's name states ("SHA256SUMS", "files.md5"), or None when it states none, more
    than one, or names a family this module does not compute (BLAKE2, SHA-3, CRC...)."""
    n = (name or "").casefold()
    if re.search(r"blake|b2sum|sha-?3|sha-?224|sha-?384|ripemd|crc|xxh|whirlpool|tiger|sm3|keccak|shake", n):
        return None
    found = {m.replace("-", "") for m in re.findall(r"md5|sha-?1(?!\d)|sha-?256|sha-?512", n)}
    return found.pop() if len(found) == 1 else None


def legacy_parse(question: str, names=(), subject_words=()) -> Parse:
    """Read a question decided by one of the older comparisons: complete when it is a yes/no question every word of
    which is a function, judgement, stating or scope word, a general word of aggregates, counts and measures, a unit,
    an artifact word or a file's name, or a word of the facts compared; it must name what (a measure or a subject
    word of the compared facts) and where (an artifact word or a file's name)."""
    toks = tokens(question)
    out = Parse(frame=bool(toks) and toks[0] in FRAME)
    own = name_tokens(*names)
    subject = {singular(w) for w in subject_words if w}
    unread = []
    for t in toks:
        s = singular(t)
        if t in NEVER:
            unread.append(t)
            continue
        if t in own or t in _LEGACY_WHERE or s in _LEGACY_WHERE:
            out.where = True
            continue
        if t in _LEGACY or s in subject or t in subject:
            out.quantity.add(t)
            continue
        if len(t) >= 2 and t.isalpha() and t not in FUNCTION and unit_of(t) is not None:
            out.quantity.add(t)
            continue
        if _consumed(t, FUNCTION | FRAME | JUDGE | STATING | ALL):
            continue
        unread.append(t)
    out.unread = tuple(dict.fromkeys(unread))
    return out


__all__ = ["ALGORITHMS", "GRAMMARS", "Grammar", "NEVER", "PLURALS", "Parse", "algorithm_in_name", "legacy_parse",
           "name_tokens", "parse", "tokens"]
