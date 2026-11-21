"""Render the pre-recorded callout clips with the Piper voice (docs/callouts.md, "Recorded voice").

Needs the optional extra `pip install -e .[voice]` (piper-tts) and ffmpeg for Opus output (without ffmpeg the clips
stay WAV). Downloads the model once into ~/.cache/exvectr-gs/piper/ (needs network that one time). Writes
web/audio/<voice-id>/<clip-id>.<ext>, manifest.json and LICENSE-VOICE.txt. Idempotent: existing clips are skipped
unless --force (or the length scale / format changed). The phrase list is web/audio/phrases.json plus the number
clips built here (num-0 .. num-99, num-100 .. num-900).

    python tools/generate_voice_clips.py [--force] [--length-scale 1.1] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PHRASES = ROOT / "web" / "audio" / "phrases.json"
VOICE_ID = "en_GB-northern_english_male-medium"
MODEL_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/northern_english_male/medium/"
CACHE = Path.home() / ".cache" / "exvectr-gs" / "piper"
LENGTH_SCALE = 1.1  # Piper length scale: > 1 speaks slower than the model default (1.0)
LICENCE = "CC-BY-SA 4.0 International"
SOURCE = "Crowdsourced high-quality UK and Ireland English Dataset (OpenSLR 83, http://www.openslr.org/83/)"
MAX_NUM = 99
SILENCE_THRESHOLD = 0.01  # of full scale; below this at the clip ends counts as silence
PAD_S = 0.02
FADE_S = 0.005

ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
        "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty", 6: "sixty", 7: "seventy", 8: "eighty", 9: "ninety"}


def number_words(n: int) -> str:
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("" if n % 10 == 0 else "-" + ONES[n % 10])
    if n % 100 == 0 and n < 1000:
        return ONES[n // 100] + " hundred"
    raise ValueError(n)


def clip_table(phrases_path: Path = PHRASES) -> dict[str, str]:
    """clip id -> spoken text: the phrase file plus the number clips."""
    data = json.loads(phrases_path.read_text(encoding="utf-8"))
    table = dict(data["phrases"])
    for n in range(MAX_NUM + 1):
        table[f"num-{n}"] = number_words(n)
    for n in range(100, 1000, 100):
        table[f"num-{n}"] = number_words(n)
    return table


def fetch_model(cache: Path = CACHE) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    for name in (f"{VOICE_ID}.onnx", f"{VOICE_ID}.onnx.json", "MODEL_CARD"):
        dst = cache / name
        if not dst.exists():
            print(f"downloading {name} ...")
            tmp = dst.with_suffix(dst.suffix + ".part")
            urllib.request.urlretrieve(MODEL_BASE + name, tmp)  # noqa: S310 (fixed https URL)
            tmp.rename(dst)
    return cache / f"{VOICE_ID}.onnx"


def trim(samples, rate: int):
    """Cut leading and trailing silence (keep PAD_S) and fade the ends by FADE_S. `samples`: float numpy array."""
    import numpy as np

    loud = np.nonzero(np.abs(samples) > SILENCE_THRESHOLD)[0]
    if loud.size == 0:
        return samples[:1]
    pad = int(PAD_S * rate)
    out = samples[max(0, loud[0] - pad) : loud[-1] + 1 + pad].copy()
    f = min(int(FADE_S * rate), out.size // 2)
    if f:
        ramp = np.linspace(0.0, 1.0, f)
        out[:f] *= ramp
        out[-f:] *= ramp[::-1]
    return out


def write_wav(path: Path, samples, rate: int) -> None:
    import numpy as np

    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def encode(wav: Path, dst: Path, ffmpeg: str | None) -> None:
    if ffmpeg is None:
        shutil.copyfile(wav, dst)
        return
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", str(wav), "-ac", "1", "-c:a", "libopus", "-b:a", "32k", str(dst)]
    subprocess.run(cmd, check=True)


def licence_text(length_scale: float) -> str:
    return (
        f"Voice: Piper {VOICE_ID} (rhasspy/piper-voices, https://huggingface.co/rhasspy/piper-voices)\n"
        f"Training data: {SOURCE}\n"
        f"Licence of the voice data and of audio generated from it: {LICENCE}\n"
        "https://creativecommons.org/licenses/by-sa/4.0/\n"
        "Finetuned from the U.S. English lessac voice (medium quality) by the Piper voices project.\n"
        f"The clips in this folder were generated with length scale {length_scale}, silence trimmed and encoded;\n"
        "they are adapted material: credit the source above, indicate the changes,\n"
        "and share the clips under the same licence.\n"
        "The licence applies to the audio in this folder only, not to the ground-station code.\n"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--force", action="store_true", help="re-render existing clips")
    ap.add_argument("--length-scale", type=float, default=LENGTH_SCALE)
    ap.add_argument("--out", type=Path, default=ROOT / "web" / "audio" / VOICE_ID, help="output folder")
    ap.add_argument("--only", help="comma separated clip ids (tests of settings)")
    args = ap.parse_args(argv)
    try:
        from piper import PiperVoice, SynthesisConfig
    except ImportError:
        print("piper-tts is not installed: pip install -e '.[voice]'", file=sys.stderr)
        return 2
    import numpy as np

    ffmpeg = shutil.which("ffmpeg")
    ext = "ogg" if ffmpeg else "wav"
    table = clip_table()
    if args.only:
        table = {k: v for k, v in table.items() if k in args.only.split(",")}
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    mpath = out / "manifest.json"
    old = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else {}
    if old.get("length_scale") != args.length_scale or old.get("format") != ext:
        args.force = args.force or bool(old)
    old_clips = old.get("clips", {}) if not args.force else {}

    voice = None
    clips: dict[str, dict] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for cid, text in table.items():
            dst = out / f"{cid}.{ext}"
            prev = old_clips.get(cid)
            if prev and prev["text"] == text and dst.exists():
                clips[cid] = prev
                continue
            if voice is None:
                voice = PiperVoice.load(fetch_model())
            cfg = SynthesisConfig(length_scale=args.length_scale)
            chunks = [np.frombuffer(c.audio_int16_bytes, dtype="<i2") for c in voice.synthesize(text, cfg)]
            rate = voice.config.sample_rate
            samples = trim(np.concatenate(chunks).astype("float32") / 32768.0, rate)
            wav = Path(tmp) / "c.wav"
            write_wav(wav, samples, rate)
            encode(wav, dst, ffmpeg)
            clips[cid] = {"file": dst.name, "text": text, "duration": round(samples.size / rate, 3)}
            print(f"{cid:28s} {clips[cid]['duration']:5.2f} s  {text}")
    manifest = {
        "voice": VOICE_ID,
        "model": f"{VOICE_ID}.onnx",
        "length_scale": args.length_scale,
        "format": ext,
        "licence": LICENCE,
        "clips": dict(sorted(clips.items())),
    }
    mpath.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    (out / "LICENSE-VOICE.txt").write_text(licence_text(args.length_scale), encoding="utf-8")
    size = sum(p.stat().st_size for p in out.iterdir())
    total = sum(c["duration"] for c in clips.values())
    print(f"{len(clips)} clips, {total:.1f} s of audio, {size / 1024:.0f} KiB in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
