"""Voice validation on RECORDED human speech (docs/voice/VOICE_VALIDATION.md).

Inputs: the public corpora prepared by scripts/voice_corpus_prepare.py (16 kHz WAV + manifest.json, outside the
repository). Live services: Whisper (vLLM, 127.0.0.1:18431) through the product's own ASR client
(voice.engines.WhisperASR.transcribe) and Kokoro (127.0.0.1:18432) for NeuroSciFi's own reply audio (echo).

Parts:
  wer       word error rate per acoustic condition (clean, noise at SNR 20/10/5 dB, reverberation, far field,
            telephone band, dialects and accents), corpus-level, with speaker-cluster bootstrap 95% intervals;
  vad       the product VAD (voice/web/vad.mjs, replayed by scripts/voice_vad_replay.mjs) on real speech onsets,
            noise-only and music-only audio, NeuroSciFi's own echo and double talk; TRAIN/TEST split by speaker,
            noise environment, music file and impulse response; retuning (if any) on TRAIN only;
  commands  intent accuracy of held-out paraphrases (tests/voice/data/command_paraphrases.json) spoken by several
            Kokoro voices and transcribed by Whisper, real recordings of single-word commands (Speech Commands), typical
            ASR errors, and the false-command rate on real ASR transcripts of real speech and on ordinary questions -
            for the grammar before (git HEAD~ copy) and after.
Output: docs/quality/evidence/voice_real_speech.json (host paths redacted as <repo>/<datasets>) and a Markdown
summary on stdout. No audio is written anywhere.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import time
import wave

import numpy as np
from scipy import signal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from neuroscifi.voice import commands as vcommands, config as vconfig, engines as vengines  # noqa: E402

RATE = 16000
FRAME = 320
SPEECH_DBFS = -26.0
SEED = 20261121
FILLERS = {"um", "uh", "er", "erm", "ah", "eh", "hmm", "mm", "mhm", "uhm", "huh", "oh"}
COMMAND_KINDS = {"confirm", "deny", "stop_speaking", "resume_speaking", "cancel_research", "show_evidence",
                 "remember", "learned_since", "simpler", "continue_research", "mode", "forget", "enable_autonomy",
                 "disable_autonomy", "handoff", "help", "history"}


# ---- audio ------------------------------------------------------------------------------------------------------

def read_wav(path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert w.getsampwidth() == 2 and w.getnchannels() == 1, path
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
        rate = w.getframerate()
    if rate != RATE:
        data = signal.resample_poly(data, RATE, rate).astype(np.float32)
    return data


def pcm16(x: np.ndarray) -> bytes:
    return (np.clip(x, -1.0, 32767 / 32768) * 32768).astype("<i2").tobytes()


def frame_rms(x: np.ndarray) -> np.ndarray:
    """RMS of consecutive 20 ms frames, as capture-worklet.mjs measures them (after 16-bit quantisation)."""
    q = np.round(np.clip(x, -1, 1) * 32767) / 32767
    n = len(q) // FRAME
    return np.sqrt(np.mean(q[:n * FRAME].reshape(n, FRAME) ** 2, axis=1))


def active_level(x: np.ndarray) -> float:
    """Active speech level (RMS over frames within 30 dB of the loudest frames), a simple ITU-T P.56 stand-in."""
    r = frame_rms(x)
    if not len(r) or r.max() <= 0:
        return 1e-9
    ref = np.percentile(r, 95)
    act = r[r > ref * 10 ** (-30 / 20)]
    return float(np.sqrt(np.mean(act ** 2))) if len(act) else float(ref)


def set_level(x: np.ndarray, dbfs: float) -> np.ndarray:
    return x * (10 ** (dbfs / 20) / max(active_level(x), 1e-9))


def rms(x):
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0


def noise_clip(noise: np.ndarray, n: int, rng) -> np.ndarray:
    if len(noise) < n:
        noise = np.tile(noise, n // len(noise) + 1)
    start = rng.integers(0, len(noise) - n + 1)
    return noise[start:start + n]


def add_noise(speech, noise, snr_db, rng):
    """Noise scaled so that active speech level / noise RMS = snr_db."""
    clip = noise_clip(noise, len(speech), rng)
    gain = active_level(speech) / max(rms(clip), 1e-9) * 10 ** (-snr_db / 20)
    return speech + clip * gain


def convolve(x, rir):
    rir = rir / max(np.max(np.abs(rir)), 1e-9)
    peak = int(np.argmax(np.abs(rir)))
    y = signal.fftconvolve(x, rir[max(0, peak - 16):])[:len(x)]   # direct path aligned: no added latency
    return y.astype(np.float32)


def telephone(x):
    """Narrow-band telephone channel: 300-3400 Hz band-pass, 8 kHz, G.711 mu-law, back to 16 kHz for the client."""
    sos = signal.butter(6, [300, 3400], btype="bandpass", fs=RATE, output="sos")
    y = signal.resample_poly(signal.sosfilt(sos, x), 1, 2)
    mu = 255.0
    y = np.clip(y, -1, 1)
    enc = np.sign(y) * np.log1p(mu * np.abs(y)) / np.log1p(mu)
    enc = np.round(enc * 127) / 127
    y = np.sign(enc) * ((1 + mu) ** np.abs(enc) - 1) / mu
    return signal.resample_poly(y, 2, 1).astype(np.float32)


# ---- text -------------------------------------------------------------------------------------------------------

_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen " \
        "seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
_ABBR = {"mr": "mister", "mrs": "missus", "dr": "doctor", "st": "saint", "vs": "versus", "ok": "okay",
         "&": "and", "%": "percent"}


def _num(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        return _ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + _num(n % 100))
    if 1100 <= n < 2000 and n % 100:              # years: nineteen eighty four
        return _num(n // 100) + " " + _num(n % 100)
    if n < 1_000_000:
        return _num(n // 1000) + " thousand" + ("" if n % 1000 == 0 else " " + _num(n % 1000))
    return " ".join(_ONES[int(d)] for d in str(n))


def words(text: str) -> list[str]:
    """Normalised words for scoring: lower case, no punctuation, numbers spelled out, fillers dropped."""
    t = text.lower().replace("’", "'").replace("-", " ")
    t = re.sub(r"(\d),(\d{3})", r"\1\2", t)
    t = re.sub(r"\d+", lambda m: " " + _num(int(m.group())) + " ", t)
    t = re.sub(r"[^a-z0-9' &%]+", " ", t)
    out = []
    for w in t.split():
        w = w.strip("'")
        w = _ABBR.get(w, w)
        if w and w not in FILLERS:
            out += w.split()
    return out


def edit_ops(ref: list[str], hyp: list[str]):
    """(substitutions, deletions, insertions) of the minimum edit alignment, and the aligned substitution pairs."""
    n, m = len(ref), len(hyp)
    d = np.zeros((n + 1, m + 1), dtype=np.int32)
    d[:, 0] = np.arange(n + 1)
    d[0, :] = np.arange(m + 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i, j] = min(d[i - 1, j] + 1, d[i, j - 1] + 1, d[i - 1, j - 1] + (ref[i - 1] != hyp[j - 1]))
    i, j, s, dl, ins, pairs = n, m, 0, 0, 0, []
    while i or j:
        if i and j and d[i, j] == d[i - 1, j - 1] + (ref[i - 1] != hyp[j - 1]):
            if ref[i - 1] != hyp[j - 1]:
                s += 1
                pairs.append((ref[i - 1], hyp[j - 1]))
            i, j = i - 1, j - 1
        elif i and d[i, j] == d[i - 1, j] + 1:
            dl, i = dl + 1, i - 1
        else:
            ins, j = ins + 1, j - 1
    return s, dl, ins, pairs


def bootstrap_wer(rows, n_boot=1000, seed=SEED):
    """Corpus WER = sum(errors)/sum(reference words); 95% interval by resampling speakers with replacement."""
    by = {}
    for r in rows:
        e, w = by.get(r["speaker"], (0, 0))
        by[r["speaker"]] = (e + r["errors"], w + r["ref_words"])
    spk = list(by)
    errs = np.array([by[s][0] for s in spk], dtype=float)
    wrds = np.array([by[s][1] for s in spk], dtype=float)
    point = errs.sum() / max(wrds.sum(), 1)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(spk), size=(n_boot, len(spk)))
    boots = errs[idx].sum(1) / np.maximum(wrds[idx].sum(1), 1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return float(point), float(lo), float(hi), len(spk)


def wilson(k, n, z=1.96):
    if n == 0:
        return (None, None)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (round(float(max(0, c - h)), 4), round(float(min(1, c + h)), 4))


# ---- services ---------------------------------------------------------------------------------------------------

def engines():
    cfg = dict(vconfig.DEFAULTS)
    eng = vengines.Engines(cfg)
    health = eng.health()
    if health["asr"]["state"] != "ready":
        raise SystemExit(f"ASR not ready: {health['asr']}")
    return eng


def transcribe_all(asr, clips, jobs=8):
    def one(c):
        for attempt in range(3):
            try:
                return asr.transcribe(pcm16(c), RATE)["text"]
            except vengines.EngineError:
                time.sleep(1.0 + attempt)
        return None
    with ThreadPoolExecutor(jobs) as pool:
        return list(pool.map(one, clips))


def tts16(tts, text, voice):
    wav = tts.synthesize(text, voice)["wav"]
    with wave.open(io.BytesIO(wav)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
        rate = w.getframerate()
    return signal.resample_poly(x, RATE, rate).astype(np.float32)


# ---- data -------------------------------------------------------------------------------------------------------

class Corpus:
    def __init__(self, prepared: Path):
        self.dir = prepared
        self.m = json.loads((prepared / "manifest.json").read_text())
        self._cache = {}

    def wav(self, rel):
        if rel not in self._cache:
            self._cache[rel] = read_wav(self.dir / rel)
        return self._cache[rel]

    def speech(self, name):
        return self.m["speech"][name]


def split_of(key: str) -> str:
    """Deterministic TRAIN/TEST assignment of a speaker, noise environment, music file or impulse response."""
    return "train" if int(hashlib.sha256(key.encode()).hexdigest(), 16) % 2 == 0 else "test"


# ---- part 1: WER ------------------------------------------------------------------------------------------------

def run_wer(corpus, asr, limit, log):
    rng = np.random.default_rng(SEED)
    clean = corpus.speech("librispeech_clean")[:limit]
    demand = [n for n in corpus.m["noise"] if n["kind"] == "demand"]
    iso = [n for n in corpus.m["noise"] if n["kind"] == "isotropic" and "largeroom" in n["id"]]
    near = [r for r in corpus.m["rir"] if r["distance"] == "near"]
    far = [r for r in corpus.m["rir"] if r["distance"] == "far"]
    conditions = {}

    def cond(name, items, make, note):
        clips = [make(it) for it in items]
        t0 = time.time()
        hyps = transcribe_all(asr, clips)
        rows, pairs = [], []
        for it, hyp in zip(items, hyps):
            ref = words(it["text"])
            if hyp is None:
                rows.append({"speaker": it["speaker"], "errors": len(ref), "ref_words": len(ref), "failed": True})
                continue
            s, dl, ins, pr = edit_ops(ref, words(hyp))
            pairs += pr
            rows.append({"speaker": it["speaker"], "errors": s + dl + ins, "ref_words": len(ref), "sub": s,
                         "del": dl, "ins": ins, "id": it["id"], "hyp": hyp, "group": it.get("group") or it.get("l1")})
        point, lo, hi, nspk = bootstrap_wer(rows)
        conditions[name] = {"wer": round(point, 4), "ci95": [round(lo, 4), round(hi, 4)], "utterances": len(rows),
                            "speakers": nspk, "ref_words": int(sum(r["ref_words"] for r in rows)),
                            "failed_requests": sum(1 for r in rows if r.get("failed")), "note": note,
                            "seconds": round(time.time() - t0, 1)}
        groups = sorted({r.get("group") for r in rows if r.get("group")})
        if groups:
            conditions[name]["by_group"] = {}
            for g in groups:
                sub = [r for r in rows if r.get("group") == g]
                p, lo2, hi2, ns = bootstrap_wer(sub)
                conditions[name]["by_group"][g] = {"wer": round(p, 4), "ci95": [round(lo2, 4), round(hi2, 4)],
                                                   "utterances": len(sub), "speakers": ns}
        top = {}
        for ref_w, hyp_w in pairs:
            top[(ref_w, hyp_w)] = top.get((ref_w, hyp_w), 0) + 1
        conditions[name]["top_substitutions"] = [f"{r} -> {h} ({n})" for (r, h), n in
                                                 sorted(top.items(), key=lambda kv: -kv[1])[:8]]
        log(f"  {name}: WER {point:.3f} [{lo:.3f}, {hi:.3f}] n={len(rows)} spk={nspk}")
        return rows, pairs

    lvl = lambda x: set_level(x, SPEECH_DBFS)   # noqa: E731
    all_rows, all_pairs = {}, []
    plan = [
        ("clean (LibriSpeech test-clean)", clean, lambda it: lvl(corpus.wav(it["wav"])), "read speech, quiet"),
        ("clean, harder speakers (LibriSpeech test-other)", corpus.speech("librispeech_other")[:limit],
         lambda it: lvl(corpus.wav(it["wav"])), "read speech, speakers with higher WER"),
    ]
    for snr in (20, 10, 5):
        plan.append((f"noise SNR {snr} dB (DEMAND)", clean,
                     lambda it, snr=snr: add_noise(lvl(corpus.wav(it["wav"])),
                                                   corpus.wav(demand[rng.integers(len(demand))]["wav"]), snr, rng),
                     "kitchen, living room, office, meeting, cafeteria, station, traffic, bus, metro, park"))
    plan.append(("reverberation (real + simulated RIRs, near)", clean,
                 lambda it: lvl(convolve(corpus.wav(it["wav"]), corpus.wav(near[rng.integers(len(near))]["wav"]))),
                 "REVERB-2014 near / AIR / simulated small and medium rooms"))
    plan.append(("far field (far RIR + room noise 15 dB, -12 dB level)", clean,
                 lambda it: add_noise(set_level(convolve(corpus.wav(it["wav"]),
                                                         corpus.wav(far[rng.integers(len(far))]["wav"])),
                                                SPEECH_DBFS - 12),
                                      corpus.wav(iso[rng.integers(len(iso))]["wav"]), 15, rng),
                 "REVERB-2014 far mic / simulated large rooms; recorded isotropic room noise"))
    plan.append(("telephone band (8 kHz, G.711)", clean, lambda it: lvl(telephone(corpus.wav(it["wav"]))),
                 "300-3400 Hz, mu-law, resampled to 16 kHz as a headset or call bridge would deliver it"))
    plan.append(("UK and Irish dialects (OpenSLR 83)", corpus.speech("openslr83_dialects")[:limit],
                 lambda it: lvl(corpus.wav(it["wav"])), "read speech, 5 dialect groups"))
    plan.append(("international accents, conversational (EdAcc)", corpus.speech("edacc_accents")[:limit],
                 lambda it: lvl(corpus.wav(it["wav"])), "spontaneous dialogue, 13 first languages"))
    for name, items, make, note in plan:
        rows, pairs = cond(name, items, make, note)
        all_rows[name] = rows
        all_pairs += pairs
    return conditions, all_rows, all_pairs


# ---- part 2: VAD, barge-in, echo --------------------------------------------------------------------------------

def replay(sequences, params_list):
    job = json.dumps({"params": params_list, "sequences": sequences})
    out = subprocess.run(["node", str(ROOT / "scripts" / "voice_vad_replay.mjs")], input=job, capture_output=True,
                         text=True, timeout=1800, check=True)
    return json.loads(out.stdout)


def onset_frame(clean: np.ndarray) -> int:
    """Oracle speech onset: first frame of the CLEAN signal within 20 dB of its active level."""
    r = frame_rms(clean)
    hits = np.nonzero(r > active_level(clean) * 10 ** (-20 / 20))[0]
    return int(hits[0]) if len(hits) else 0


def build_vad_sets(corpus, tts, log, n_speech=160, reply_texts=None):
    rng = np.random.default_rng(SEED + 1)
    demand = [n for n in corpus.m["noise"] if n["kind"] == "demand"]
    music = corpus.m["music"]
    near = [r for r in corpus.m["rir"] if r["distance"] == "near"]
    speech = corpus.speech("librispeech_clean") + corpus.speech("librispeech_other")
    sets = {"train": [], "test": []}

    def add(split, seq):
        seq["rms"] = [round(float(v), 6) for v in seq["rms"]]
        sets[split].append(seq)

    def room(n, level_db):
        env = demand[rng.integers(len(demand))]
        clip = noise_clip(corpus.wav(env["wav"]), n, rng)
        return clip * (10 ** (level_db / 20) / max(rms(clip), 1e-9))

    # speech onsets while listening: detection and latency (speech level varies by +-7 dB around -27 dBFS)
    for it in speech[:n_speech * 2]:
        lead = int(rng.uniform(1.0, 2.0) * RATE)
        level = rng.uniform(-34, -20)
        x = set_level(corpus.wav(it["wav"])[: 6 * RATE], level)
        snr = float(rng.choice([30, 20, 10, 5]))
        clean = np.concatenate([np.zeros(lead, np.float32), x, np.zeros(RATE, np.float32)])
        noisy = clean + room(len(clean), level - snr)
        add(split_of("spk:" + it["speaker"]), {"id": f"onset:{it['id']}", "kind": "onset", "snr": snr,
                                               "level": round(level, 1), "onset": onset_frame(clean),
                                               "end": (lead + len(x)) // FRAME, "rms": frame_rms(noisy), "playing": 0})
    # single command words recorded by real people (Speech Commands): "stop", "no", "yes", "go"
    for c in corpus.m["commands"]:
        if c["text"] not in ("stop", "no", "yes", "go"):
            continue
        x = set_level(corpus.wav(c["wav"]), SPEECH_DBFS + rng.uniform(-6, 6))
        clean = np.concatenate([np.zeros(RATE, np.float32), x, np.zeros(RATE, np.float32)])
        add(split_of("spk:" + c["speaker"]), {"id": f"word:{c['id']}", "kind": "word", "snr": 20.0,
                                              "onset": onset_frame(clean), "end": (RATE + len(x)) // FRAME,
                                              "rms": frame_rms(clean + room(len(clean), SPEECH_DBFS - 20)),
                                              "playing": 0})
    # noise-only and music-only, 30 s segments at levels a speaker at -26 dBFS would see at SNR 20/10/5 dB
    for env in demand:
        split = split_of("env:" + env["env"])
        for snr in (20, 10, 5):
            for k in range(2):
                clip = noise_clip(corpus.wav(env["wav"]), 30 * RATE, rng)
                clip = clip * (10 ** ((SPEECH_DBFS - snr) / 20) / max(rms(clip), 1e-9))
                r = frame_rms(clip)
                add(split, {"id": f"noise:{env['env']}:{snr}:{k}", "kind": "noise", "snr": snr, "rms": r,
                            "playing": 0})
                add(split, {"id": f"noiseP:{env['env']}:{snr}:{k}", "kind": "noise_playing", "snr": snr, "rms": r,
                            "playing": 1})
    for mf in music:
        split = split_of("music:" + mf["id"])
        for lvl_db in (-36, -30):
            clip = noise_clip(corpus.wav(mf["wav"]), 30 * RATE, rng)
            clip = clip * (10 ** (lvl_db / 20) / max(rms(clip), 1e-9))
            r = frame_rms(clip)
            add(split, {"id": f"music:{mf['id']}:{lvl_db}", "kind": "music", "level": lvl_db, "rms": r,
                        "vocals": mf.get("vocals"), "playing": 0})
            add(split, {"id": f"musicP:{mf['id']}:{lvl_db}", "kind": "music_playing", "level": lvl_db, "rms": r,
                        "vocals": mf.get("vocals"), "playing": 1})
    # echo of NeuroSciFi's own replies (Kokoro af_heart, the default voice) and double talk. The echo path: a mild
    # loudspeaker non-linearity, a recorded room impulse response, 20-150 ms of delay and a level set by the
    # echo-to-speech ratio (ESR: echo level at the mic relative to -26 dBFS user speech); room noise 25 dB below.
    replies = [tts16(tts, t, "af_heart") for t in reply_texts]
    log(f"  synthesised {len(replies)} replies ({sum(len(r) for r in replies) / RATE:.0f} s of NeuroSciFi speech)")
    users = speech[n_speech * 2:n_speech * 2 + 240]
    stops = [c for c in corpus.m["commands"] if c["text"] == "stop"]
    for i, rep in enumerate(replies):
        for esr in (-20, -10, 0, 6):
            for k in range(3):
                rir = near[rng.integers(len(near))]
                split = split_of(f"echo:{i}:{rir['id']}")
                delay = int(rng.uniform(0.02, 0.15) * RATE)
                echo = convolve(np.tanh(1.5 * rep) / 1.5, corpus.wav(rir["wav"]))
                echo = set_level(echo, SPEECH_DBFS + esr)
                lead = int(0.3 * RATE)
                total = lead + delay + len(echo) + RATE
                cap = np.zeros(total, np.float32)
                cap[lead + delay:lead + delay + len(echo)] += echo
                cap = cap + room(total, SPEECH_DBFS - 25)
                playing = np.zeros(total // FRAME, np.int8)
                playing[lead // FRAME:(lead + len(rep)) // FRAME] = 1
                play_end = (lead + len(rep)) // FRAME
                add(split, {"id": f"echo:{i}:{esr}:{k}", "kind": "echo", "esr": esr, "rms": frame_rms(cap),
                            "playing": playing.tolist(), "play_end": play_end})
                # double talk: a sentence (LibriSpeech) or the word "stop" (Speech Commands) during the reply
                for kind, pool in (("double_talk", users), ("stop_barge", stops)):
                    u = pool[(i * 12 + (esr + 20) // 2 + k) % len(pool)]
                    us = set_level(corpus.wav(u["wav"])[: 4 * RATE], SPEECH_DBFS + rng.uniform(-6, 6))
                    at = lead + int(rng.uniform(1.0, max(1.2, len(rep) / RATE - 2.5)) * RATE)
                    end = min(total, at + len(us))
                    clean_u = np.zeros(total, np.float32)
                    clean_u[at:end] = us[: end - at]
                    usplit = split_of("spk:" + u["speaker"])   # TEST only when the echo and the speaker are TEST
                    add("test" if split == usplit == "test" else "train",
                        {"id": f"{kind}:{i}:{esr}:{k}", "kind": kind, "esr": esr, "rms": frame_rms(cap + clean_u),
                         "playing": playing.tolist(), "onset": onset_frame(clean_u), "user_end": end // FRAME,
                         "play_end": play_end})
    return sets


def _utterances(events):
    """Confirmed utterance intervals [(start, confirm, end)] from replayed events."""
    out, start, conf = [], None, None
    for f, kind, _ in events:
        if kind == "start":
            start, conf = f, None
        elif kind == "confirm" and start is not None:
            conf = f
        elif kind == "end" and start is not None:
            if conf is not None:
                out.append((start, conf, f))
            start, conf = None, None
    if start is not None and conf is not None:
        out.append((start, conf, 10 ** 9))
    return out


def score_vad(seqs, results):
    """Per kind and condition: rates with Wilson 95% intervals and latencies in ms (from the oracle onset)."""
    ev_by = {r["id"]: r["events"] for r in results}
    acc = {}
    for s in seqs:
        ev = ev_by[s["id"]]
        k = s["kind"]
        a = acc.setdefault((k, s.get("snr", s.get("esr", s.get("level")))),
                           {"n": 0, "hit": 0, "lat": [], "false": 0, "minutes": 0.0})
        a["n"] += 1
        barges = [e[0] for e in ev if e[1] == "barge"]
        if k in ("onset", "word"):
            utts = _utterances(ev)
            a["false"] += any(end < s["onset"] - 5 for _, _, end in utts)      # a whole utterance before speech
            hit = [(st, cf) for st, cf, end in utts if end >= s["onset"] and cf <= s["end"] + 35]
            if hit:
                a["hit"] += 1
                a["lat"].append(max(0, hit[0][1] - s["onset"]) * 20)
        elif k in ("noise", "music"):
            n = len(_utterances(ev))
            a["false"] += n
            a["hit"] += bool(n)
            a["minutes"] += len(s["rms"]) * 20 / 60000
        elif k in ("noise_playing", "music_playing"):
            a["false"] += len(barges)
            a["hit"] += bool(barges)
            a["minutes"] += len(s["rms"]) * 20 / 60000
        elif k == "echo":
            b = [f for f in barges if f <= s["play_end"]]
            a["hit"] += bool(b)
        elif k in ("double_talk", "stop_barge"):
            b = [f for f in barges if f <= s["play_end"]]
            before = [f for f in b if f < s["onset"] - 5]
            after = [f for f in b if s["onset"] - 5 <= f <= s["user_end"] + 10]
            a["false"] += bool(before)
            if not before and after:
                a["hit"] += 1
                a["lat"].append(max(0, after[0] - s["onset"]) * 20)
    out = {}
    for (k, cond), a in sorted(acc.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        row = {"n": a["n"]}
        if k in ("onset", "word"):
            row.update(detected=a["hit"] / a["n"], detected_ci95=wilson(a["hit"], a["n"]),
                       false_utterance_before=a["false"] / a["n"])
        elif k in ("noise", "music", "noise_playing", "music_playing"):
            row.update(segments_with_trigger=a["hit"] / a["n"], segments_ci95=wilson(a["hit"], a["n"]),
                       triggers_per_minute=round(a["false"] / max(a["minutes"], 1e-9), 3),
                       minutes=round(a["minutes"], 1))
        elif k == "echo":
            row.update(falsely_interrupted=a["hit"] / a["n"], ci95=wilson(a["hit"], a["n"]))
        else:
            row.update(detected=a["hit"] / a["n"], detected_ci95=wilson(a["hit"], a["n"]),
                       false_before_user=a["false"] / a["n"])
        if a["lat"]:
            lat = np.array(a["lat"])
            row.update(latency_ms_p50=float(np.percentile(lat, 50)), latency_ms_p90=float(np.percentile(lat, 90)))
        for key in list(row):
            if isinstance(row[key], float):
                row[key] = round(row[key], 4)
        out[f"{k}@{cond}"] = row
    return out


def _get(sc, key, field, default=0.0):
    return sc.get(key, {}).get(field, default)


def listen_objective(sc):
    """Listening (selection rule, docs/voice/VOICE_VALIDATION.md): false utterances from noise at SNR 20 and 10 dB
    <= 1 per minute; then maximise detection of sentences (SNR 30/20/10) and single words; then latency."""
    false_rate = max(_get(sc, f"noise@{s}", "triggers_per_minute") for s in (20, 10))
    feasible = false_rate <= 1.0
    miss = sum(1 - _get(sc, f"onset@{s}", "detected") for s in (30.0, 20.0, 10.0)) + \
        2 * (1 - _get(sc, "word@20.0", "detected"))
    lat = _get(sc, "onset@20.0", "latency_ms_p50", 2000) / 1000
    return (0 if feasible else 1, round(miss + 0.2 * lat + 0.05 * false_rate, 4))


def barge_objective(sc):
    """Barge-in: own echo falsely interrupts <= 5% of replies at ESR <= -10 dB (echo cancellation working), noise
    while playing <= 0.5/min at SNR >= 10 dB, background music (-36 dBFS) <= 1/min; then maximise detection of user
    speech and of the word "stop" during playback at ESR -20/-10 dB; then latency."""
    echo = max(_get(sc, f"echo@{e}", "falsely_interrupted") for e in (-20, -10))
    noise = max(_get(sc, f"noise_playing@{s}", "triggers_per_minute") for s in (20, 10))
    music = _get(sc, "music_playing@-36", "triggers_per_minute")
    feasible = echo <= 0.05 and noise <= 0.5 and music <= 1.0
    miss = sum(1 - _get(sc, f"{k}@{e}", "detected") for k in ("double_talk", "stop_barge") for e in (-20, -10))
    lat = _get(sc, "stop_barge@-10", "latency_ms_p50", 2000) / 1000
    return (0 if feasible else 1, round(miss + 0.2 * lat + echo + 0.05 * (noise + music), 4))


LISTEN_KINDS = ("onset", "word", "noise", "music")
GRID_LISTEN = [dict(SPEECH_K=k, SPEECH_MIN=m, MIN_SPEECH_MS=ms, FLOOR_ALPHA=fa)
               for k in (2.5, 3, 4, 5) for m in (0.006, 0.008, 0.012) for ms in (120, 160, 200, 250)
               for fa in (0.05, 0.1)]
GRID_BARGE = [dict(BARGE_K=k, BARGE_MIN=m, BARGE_MS=ms)
              for k in (2, 2.5, 3, 4, 5) for m in (0.01, 0.015, 0.02, 0.03) for ms in (120, 160, 200, 250, 300)]


# The thresholds as they were before 0.7.0's validation (tuned on synthetic speech only).
BEFORE = dict(SPEECH_K=3, SPEECH_MIN=0.012, MIN_SPEECH_MS=250, FLOOR_ALPHA=0.05, BARGE_K=5, BARGE_MIN=0.03,
              BARGE_MS=300)


def run_vad(corpus, tts, log, reply_texts, tune=True):
    """BEFORE thresholds scored on TRAIN; each grid scored on TRAIN only; the winners are what vad.mjs ships (checked
    here); TEST is replayed once with BEFORE and with the shipped thresholds."""
    sets = build_vad_sets(corpus, tts, log, reply_texts=reply_texts)
    log(f"  sequences: train {len(sets['train'])}, test {len(sets['test'])}")
    res = {"counts": {k: len(v) for k, v in sets.items()}, "before_params": BEFORE}
    train_l = [s for s in sets["train"] if s["kind"] in LISTEN_KINDS]
    train_b = [s for s in sets["train"] if s["kind"] not in LISTEN_KINDS]
    base = replay(sets["train"], [BEFORE, {}])
    shipped = {k: base["defaults"][k] for k in BEFORE}
    res["shipped_params"] = shipped
    res["train_before"] = score_vad(sets["train"], base["results"][0])
    res["train_before_objective"] = {"listen": listen_objective(res["train_before"]),
                                     "barge": barge_objective(res["train_before"])}
    res["train_shipped"] = score_vad(sets["train"], base["results"][1])
    log(f"  TRAIN before objective {res['train_before_objective']}")
    if tune:   # sequential and explicit: listening on top of BEFORE, then barge-in on top of that winner
        gl = replay(train_l, [{**BEFORE, **g} for g in GRID_LISTEN])["results"]
        best_l = min((listen_objective(score_vad(train_l, r)), i) for i, r in enumerate(gl))
        listen = {**BEFORE, **GRID_LISTEN[best_l[1]]}
        gb = replay(train_b, [{**listen, **g} for g in GRID_BARGE])["results"]
        best_b = min((barge_objective(score_vad(train_b, r)), i) for i, r in enumerate(gb))
        tuned = {**listen, **GRID_BARGE[best_b[1]]}
        res["grid"] = {"listen": len(GRID_LISTEN), "barge": len(GRID_BARGE)}
        res["train_tuned_objective"] = {"listen": best_l[0], "barge": best_b[0]}
        res["tuned_params"] = tuned
        res["shipped_equals_tuned"] = tuned == shipped
        log(f"  TRAIN tuned objective {res['train_tuned_objective']} params {tuned} (shipped: {tuned == shipped})")
    test = replay(sets["test"], [BEFORE, {}])["results"]
    res["test_before"] = score_vad(sets["test"], test[0])
    res["test_shipped"] = score_vad(sets["test"], test[1])
    return res, sets


# ---- part 3: commands -------------------------------------------------------------------------------------------

def baseline_commands():
    """The grammar as it was before this change (git), loaded as a module for the before/after comparison."""
    src = subprocess.run(["git", "-C", str(ROOT), "show", "94ccb09:src/neuroscifi/voice/commands.py"],
                         capture_output=True, text=True, check=True).stdout
    spec = importlib.util.spec_from_loader("commands_before", loader=None)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["commands_before"] = mod          # dataclasses look their module up while the class is built
    exec(compile(src, "commands_before.py", "exec"), mod.__dict__)
    return mod


def label(intent):
    if intent.kind in ("mode", "forget"):
        return f"{intent.kind}:{intent.arg}"
    return intent.kind


def is_command(intent):
    return intent.kind in COMMAND_KINDS


ASR_ERRORS = [   # typical Whisper surface variants and confusions (observed in this run's real transcripts or common)
    (r"^(.*)$", r"\1."), (r"^(.*)$", r"\1!"), (r"^(.*)$", r"\1?"), (r"^(\w)", lambda m: m.group(1).upper()),
    (r"^", "Um, "), (r"^", "Okay. "), (r"\bquiet\b", "quite"), (r"\bstop\b", "Stop."), (r"\bgo on\b", "Go on."),
    (r"\bhold on\b", "Hold on,"), (r"\bresearching\b", "re-searching"), (r"\bcontinue\b", "continued"),
    (r"\bsources\b", "sauces"), (r"\bno\b", "Know"), (r"\bthat's\b", "that is"), (r"\bi\b", "I"),
    (r"\bsimpler\b", "simple"), (r"\bevidence\b", "evidences"), (r"\bshow\b", "So"), (r"\bresume\b", "resumé"),
]


def asr_variants(text, rng, mined):
    out = []
    for pat, rep in ASR_ERRORS:
        v = re.sub(pat, rep, text, count=1, flags=re.IGNORECASE)
        if v != text:
            out.append(v)
    ws = text.split()
    for i, w in enumerate(ws):
        for hyp in mined.get(w.lower(), [])[:2]:
            out.append(" ".join(ws[:i] + [hyp] + ws[i + 1:]))
    rng.shuffle(out)
    return out[:4]


PARAPHRASE_SETS = {   # tests/voice/data: how each list relates to the grammar work (docs/voice/VOICE_VALIDATION.md)
    "author": ("command_paraphrases.json", "written before the synonyms by their author: not held out"),
    "independent": ("command_paraphrases_independent.json",
                    "separate model instance; its misses were seen once and motivated the frames: not held out"),
    "fresh": ("command_paraphrases_fresh.json", "third model instance, written after the grammar was frozen; "
                                                "evaluated once: HELD OUT"),
}


def run_commands(corpus, eng, wer_rows, wer_pairs, log):
    before = baseline_commands()
    grammars = {"before": before.parse, "after": vcommands.parse}
    rng = random.Random(SEED)
    mined = {}
    for ref, hyp in wer_pairs:
        mined.setdefault(ref, [])
        if hyp not in mined[ref]:
            mined[ref].append(hyp)
    voices = ["am_adam", "bf_emma", "bm_lewis", "af_nicole", "am_onyx", "bf_isabella"]
    demand = [n for n in corpus.m["noise"] if n["kind"] == "demand"]
    nrng = np.random.default_rng(SEED + 7)
    res = {"sets": {}}
    for name, (fname, note) in PARAPHRASE_SETS.items():
        data = json.loads((ROOT / "tests" / "voice" / "data" / fname).read_text())
        texts = [(t, lab) for t, lab in data["commands"]]
        out = {"note": note, "commands": len(texts), "ordinary": len(data["ordinary"])}
        out["text_accuracy"] = {g: round(sum(label(p(t)) == lab for t, lab in texts) / len(texts), 4)
                                for g, p in grammars.items()}
        variants = [(v, lab) for t, lab in texts for v in asr_variants(t, rng, mined)]
        out["asr_error_variants"] = {g: round(sum(label(p(t)) == lab for t, lab in variants) / len(variants), 4)
                                     for g, p in grammars.items()}
        out["asr_error_variants_n"] = len(variants)
        out["ordinary_text_false_commands"] = {g: sum(is_command(p(t)) for t in data["ordinary"])
                                               for g, p in grammars.items()}
        # spoken by two voices each (never NeuroSciFi's own af_heart), quiet and with 10 dB room noise, via Whisper
        clips, meta = [], []
        for i, (t, lab) in enumerate(texts):
            for v in (voices[i % len(voices)], voices[(i + 3) % len(voices)]):
                x = set_level(tts16(eng.tts, t, v), SPEECH_DBFS)
                x = np.concatenate([np.zeros(RATE // 4, np.float32), x, np.zeros(RATE // 4, np.float32)])
                clips += [x, add_noise(x, corpus.wav(demand[nrng.integers(len(demand))]["wav"]), 10, nrng)]
                meta += [(t, lab, v, "quiet"), (t, lab, v, "noise10")]
        hyps = transcribe_all(eng.asr, clips)
        spoken = [{"text": t, "expected": lab, "voice": v, "condition": c, "asr": h,
                   **{g: label(p(h or "")) for g, p in grammars.items()}} for (t, lab, v, c), h in zip(meta, hyps)]
        out["spoken"] = {c: {g: round(sum(x[g] == x["expected"] for x in spoken if x["condition"] == c) /
                                      max(1, sum(1 for x in spoken if x["condition"] == c)), 4) for g in grammars}
                         for c in ("quiet", "noise10")}
        out["spoken_n"] = len(spoken)
        out["spoken_misses_after"] = [{k: x[k] for k in ("text", "expected", "asr", "after", "condition")}
                                      for x in spoken if x["after"] != x["expected"]][:25]
        ohyps = transcribe_all(eng.asr, [set_level(tts16(eng.tts, t, voices[i % len(voices)]), SPEECH_DBFS)
                                         for i, t in enumerate(data["ordinary"])])
        out["ordinary_spoken_false_commands"] = {g: sum(is_command(p(h or "")) for h in ohyps)
                                                 for g, p in grammars.items()}
        res["sets"][name] = out
        log(f"  {name}: text {out['text_accuracy']} spoken {out['spoken']}")
    # real recordings of single command words (Speech Commands v0.02 test set), transcribed by Whisper
    cmd = corpus.m["commands"]
    real = transcribe_all(eng.asr, [set_level(corpus.wav(c["wav"]), SPEECH_DBFS) for c in cmd])
    expect_word = {"stop": "stop_speaking", "no": "deny"}
    per = {}
    for c, h in zip(cmd, real):
        w = c["text"]
        p = per.setdefault(w, {"n": 0, **{g: 0 for g in grammars}, "asr_exact": 0})
        p["n"] += 1
        p["asr_exact"] += words(h or "") == [w]
        for g, parse in grammars.items():
            lab = label(parse(h or ""))
            p[g] += (lab == expect_word[w]) if w in expect_word else (not is_command(parse(h or "")))
    res["speech_commands_real"] = {"expected": "stop -> stop_speaking, no -> deny, other words -> no command",
                                   "per_word": per}
    # false commands on real ASR transcripts of real speech (every hypothesis of the WER run)
    real_hyps = [r["hyp"] for rows in wer_rows.values() for r in rows if r.get("hyp")]
    res["false_command_real_transcripts"] = {
        g: {"n": len(real_hyps), "commands": sum(is_command(p(h)) for h in real_hyps)} for g, p in grammars.items()}
    res["false_command_real_examples"] = {g: [h for h in real_hyps if is_command(p(h))][:15]
                                          for g, p in grammars.items()}
    res["fuzzy_examples"] = [
        {"heard": h, "intent": label(vcommands.parse(h)), "fuzzy": vcommands.parse(h).fuzzy}
        for h in ("stop talkin", "go on pleas", "cancel the reseach", "what was i re searching", "show me the evidense")]
    return res


# ---- main -------------------------------------------------------------------------------------------------------

REPLY_TEXTS = [
    "Here is what I found. Three independent sources agree that sleep helps consolidate memories, especially during "
    "deep slow wave sleep, and two of them report effects within a single night.",
    "The boiling point of water falls as air pressure falls. At the summit of Mount Everest it is about seventy one "
    "degrees Celsius, so tea brewed there is noticeably weaker.",
    "I could not confirm that claim. One source says the bridge opened in nineteen thirty two, but the city's own "
    "archive gives a different year, so I marked it as disputed.",
    "Jupiter has ninety five officially recognised moons according to the most recent count from the International "
    "Astronomical Union. Most of them are small and irregular.",
    "Weather is what happens over hours and days, like today's rain. Climate is the pattern of weather over decades, "
    "such as how warm and wet a region usually is.",
    "The research is still running. I have read six sources so far and I am checking whether the newest numbers are "
    "really current before I write the answer.",
    "Electric cars usually cost less per kilometre to run than petrol cars, mostly because electricity is cheaper "
    "than fuel and the motors need less maintenance.",
    "Noise cancelling headphones listen to the sound around you with small microphones and play back the opposite "
    "wave, so the two cancel out before the sound reaches your ear.",
]


def redact(obj, datasets):
    text = json.dumps(obj)
    text = text.replace(str(ROOT), "<repo>").replace(str(datasets), "<datasets>").replace(str(Path.home()), "~")
    return json.loads(text)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--datasets", default=os.path.expanduser("~/.local/share/neuroscifi/datasets"))
    ap.add_argument("--parts", default="wer,vad,commands")
    ap.add_argument("--limit", type=int, default=400, help="utterances per WER condition (at most)")
    ap.add_argument("--out", default=str(ROOT / "docs" / "quality" / "evidence" / "voice_real_speech.json"))
    a = ap.parse_args(argv)
    log = lambda m: print(m, file=sys.stderr, flush=True)   # noqa: E731
    datasets = Path(a.datasets)
    corpus = Corpus(datasets / "prepared")
    eng = engines()
    parts = set(a.parts.split(","))
    out = json.loads(Path(a.out).read_text()) if Path(a.out).exists() else {}
    out.update({"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "asr": {"engine": eng.asr.name, "model": eng.asr.model, "url": eng.asr.url,
                        "client": "neuroscifi.voice.engines.WhisperASR.transcribe"},
                "tts": {"engine": eng.tts.name if hasattr(eng.tts, "name") else "kokoro", "url": eng.tts.url},
                "speech_level_dbfs": SPEECH_DBFS, "seed": SEED})
    wer_rows, wer_pairs = {}, []
    cache = datasets / "prepared" / "wer_rows.json"      # real transcripts of the public corpora (outside the repo)
    if "wer" in parts:
        log("WER")
        out["wer"], wer_rows, wer_pairs = run_wer(corpus, eng.asr, a.limit, log)
        cache.write_text(json.dumps({"rows": wer_rows, "pairs": wer_pairs}))
    elif "commands" in parts:
        cached = json.loads(cache.read_text())
        wer_rows, wer_pairs = cached["rows"], [tuple(p) for p in cached["pairs"]]
    if "vad" in parts:
        log("VAD / barge-in / echo")
        out["vad"], _ = run_vad(corpus, eng.tts, log, REPLY_TEXTS)
    if "commands" in parts:
        log("commands")
        out["commands"] = run_commands(corpus, eng, wer_rows, wer_pairs, log)
    Path(a.out).write_text(json.dumps(redact(out, datasets), indent=1) + "\n")
    log(f"wrote {a.out}")


if __name__ == "__main__":
    main()
