"""`nsf imagine ...`: exact what-ifs, the value of the next reading, retrospection, graph forecasts, a declared causal
model, and the Web Cortex's scenario records (ADR-052). Every answer is labelled imagined, not evidence; only --keep
(and the scenario records) write, and only to the imagined store."""
import argparse
import json

from .errors import ValidationError

HELP = ("ask what would follow if things were different: computed on a private copy, never written as fact "
        "(answers are labelled 'imagined, not evidence')")


def _reading(text):
    """--add FACTOR=READING[xN]: READING is 0, 1, agree or disagree; N readings (default 1)."""
    if "=" not in text:
        raise ValidationError(f"--add takes FACTOR=READING[xN] (for example partner:p=agreex4), got {text!r}")
    factor, rest = text.rsplit("=", 1)
    reading, _, count = rest.partition("x")
    reading = int(reading) if reading in ("0", "1") else reading
    try:
        count = int(count) if count else 1
    except ValueError:
        raise ValidationError(f"the count after x must be a whole number, got {text!r}") from None
    return {"factor": factor, "reading": reading, "count": count}


def _kept(db, answer, args, now):
    if not getattr(args, "keep", False):
        return answer
    from . import imagine
    return answer | {"kept_as": imagine.keep(db, answer, now)}


def _what_if(db, args, now):
    from . import imagine
    answer = imagine.what_if(db, args.epoch, flip=args.flip or (), drop=args.drop or (),
                             add=[_reading(a) for a in args.add or ()], now=now)
    return _kept(db, answer, args, now)


def _questions(db, args, now):
    from . import imagine
    return _kept(db, imagine.question_values(db, args.epoch, now=now), args, now)


def _retrospect(db, args, now):
    from . import imagine
    return _kept(db, imagine.retrospect(db, args.epoch, args.factor, limit=args.limit, top=args.top), args, now)


def _json_arg(text, what):
    try:
        return json.loads(text)
    except ValueError as error:
        raise ValidationError(f"{what} is not JSON: {error}") from None


def _forecast(db, args, now):
    from . import forecast
    action = None if args.push is None else {"population": int(args.push[0]), "push": args.push[1]}
    return forecast.forecast(db, args.context, _json_arg(args.state, "--state"), args.horizon,
                             population=args.population, action=action)


SCM_LIMIT = 1 << 20      # bytes of an `nsf imagine scm` question file read at most


def _scm(args):
    """One question from a JSON file: read bounded and without blocking (a FIFO or a device is refused), and any
    malformed question is a one-line error, never a traceback (review1 S8)."""
    from .. import causal
    from ..resources import shared
    try:
        text = shared.read_text(args.file.resolve(), limit=SCM_LIMIT)
    except shared.NotAFile:
        raise ValidationError(f"cannot read {args.file}: not a plain file") from None
    except OSError as error:
        raise ValidationError(f"cannot read {args.file}: {error.strerror or error}") from None
    except ValueError:
        raise ValidationError(f"{args.file} is larger than {SCM_LIMIT} bytes") from None
    try:
        query = _json_arg(text, str(args.file))
    except RecursionError:
        raise ValidationError(f"{args.file} is nested too deeply to be read") from None
    try:
        return causal.answer(query, front_door=args.front_door)
    except ValueError as error:
        raise ValidationError(str(error)) from None
    except (KeyError, TypeError, AttributeError, IndexError, ZeroDivisionError, RecursionError,
            OverflowError) as error:
        raise ValidationError(f"the question in {args.file} is malformed ({type(error).__name__})") from None


def _scenarios(db, args, now):
    from ..webcortex import scenarios, state
    if not state.has_tables(db):
        raise ValidationError("this individual has no Web Cortex tables")
    if args.prediction is not None:
        if args.record:
            return {"record_id": scenarios.imagine(db, args.prediction, now)}
        return scenarios.futures(db, args.prediction)
    settled = scenarios.settle(db, now) if scenarios.enabled(db) else []
    return {"settled_now": settled, "errors": scenarios.errors(db), "enabled": scenarios.enabled(db)}


def register(sub, *, with_db, run):
    from pathlib import Path
    from . import store
    p = sub.add_parser("imagine", help=HELP, description=HELP + ".")
    isub = p.add_subparsers(dest="imagine_what", required=True)

    q = isub.add_parser("what-if", help="the exact beliefs had readings been different, absent, or more")
    store.add_db_argument(q)
    q.add_argument("epoch")
    q.add_argument("--flip", action="append", metavar="ID", help="a reading that read the other way (evidence id, "
                                                                  "or #SEQ for a consolidated reading); repeatable")
    q.add_argument("--drop", action="append", metavar="ID", help="a reading that never arrived; repeatable")
    q.add_argument("--add", action="append", metavar="FACTOR=READING[xN]",
                   help="N more readings of camera:R, partner:P or reference:R reading 0, 1, agree or disagree "
                        "(for example partner:p=agreex4); repeatable")
    q.add_argument("--keep", action="store_true", help="also keep the answer in the imagined store")
    q.set_defaults(handler=with_db(_what_if))

    q = isub.add_parser("questions", help="for each reading that could come next: the chance of each outcome, the "
                                          "beliefs after it, and how much it would tell (bits)")
    store.add_db_argument(q)
    q.add_argument("epoch")
    q.add_argument("--keep", action="store_true", help="also keep the answer in the imagined store")
    q.set_defaults(handler=with_db(_questions))

    q = isub.add_parser("retrospect", help="which earlier reading or answer most changed a belief (each removed in "
                                           "turn on a private copy)")
    store.add_db_argument(q)
    q.add_argument("epoch")
    q.add_argument("factor", nargs="?", default="mechanism", help="mechanism (default), camera:R or partner:P")
    q.add_argument("--limit", type=int, default=200, help="how many of the latest readings to try (1 to 2000)")
    q.add_argument("--top", type=int, default=10, help="how many results to show (1 to 100)")
    q.add_argument("--keep", action="store_true", help="also keep the answer in the imagined store")
    q.set_defaults(handler=with_db(_retrospect))

    q = isub.add_parser("forecast", help="expected targets several steps ahead from a context's active graph, with "
                                         "their spread; a push only where the recorded data supports it")
    store.add_db_argument(q)
    q.add_argument("context")
    q.add_argument("--state", required=True, help="the current state: a JSON list, one number per population")
    q.add_argument("--horizon", type=int, default=1, help="steps ahead (1 = the graph's own next-step prediction)")
    q.add_argument("--population", type=int, help="only this population")
    q.add_argument("--push", nargs=2, type=float, metavar=("POPULATION", "AMOUNT"),
                   help="push one population by an amount at the next step")
    q.set_defaults(handler=with_db(_forecast))

    q = isub.add_parser("scm", help="answer a question about a declared linear causal model (JSON file): what is "
                                    "seen, what a change would do, what would have happened")
    q.add_argument("file", type=Path)
    q.add_argument("--front-door", action=argparse.BooleanOptionalAction, default=True,
                   help="when a hidden common cause acts on the cause, answer by front-door adjustment where the "
                        "criterion holds, saying so (default: on; --no-front-door declines those questions)")
    q.set_defaults(handler=run(_scm))

    q = isub.add_parser("scenarios", help="the Web Cortex's scenario records: their prediction errors, or the "
                                          "futures of one prediction")
    store.add_db_argument(q)
    q.add_argument("--prediction", type=int, help="show the template's futures for this prediction")
    q.add_argument("--record", action="store_true", help="with --prediction: keep them in the imagined store")
    q.set_defaults(handler=with_db(_scenarios))
