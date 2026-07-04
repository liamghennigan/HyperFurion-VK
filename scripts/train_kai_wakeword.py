#!/usr/bin/env python3
"""Generate synthetic training data for a custom "Kai" openWakeWord model.

A wake word needs many varied utterances of the word. This script uses
piper (already in the local stack) to synthesize a batch of "Kai" clips —
across voices, speeds, and small phrase variations — into a positives
directory, then prints the openWakeWord training command that turns them
into a model.

It deliberately does the ONE product-specific part (minting the positives)
and hands off the heavy training to openWakeWord itself, rather than
pretending to wrap a training pipeline it cannot verify here.

Usage:
    python scripts/train_kai_wakeword.py --out ~/ai/wake/kai
    # then follow the printed openWakeWord training step, and point
    # [wake] model_path at the resulting .onnx / .tflite.

Requires: piper on PATH, and (for the training step) the [wake] extra:
    pip install 'hyperfurion-vk[wake]'
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# Spoken variations of the wake word — a wake model generalizes better when
# the positive set spans natural phrasings, not one clipped token.
PHRASES = [
    "Kai",
    "Kai,",
    "Hey Kai",
    "Okay Kai",
    "Kai?",
    "Kai please",
    "Um, Kai",
]


def _find_piper() -> str:
    piper = shutil.which("piper")
    if not piper:
        sys.exit("piper not found on PATH — install it first (it's in the local stack).")
    return piper


def synthesize(out_dir: Path, voice: str, count_per_phrase: int) -> int:
    piper = _find_piper()
    out_dir.mkdir(parents=True, exist_ok=True)
    made = 0
    for phrase in PHRASES:
        for i in range(count_per_phrase):
            wav = out_dir / f"kai_{made:04d}.wav"
            try:
                subprocess.run(
                    [piper, "--model", voice, "--output_file", str(wav)],
                    input=phrase.encode("utf-8"),
                    check=True,
                    capture_output=True,
                )
                made += 1
            except subprocess.CalledProcessError as exc:
                sys.stderr.write(
                    f"piper failed for {phrase!r}: {exc.stderr.decode(errors='ignore')}\n"
                )
    return made


def main() -> None:
    ap = argparse.ArgumentParser(description="Synthesize 'Kai' wake-word positives")
    ap.add_argument("--out", required=True, help="output directory for positives")
    ap.add_argument(
        "--voice",
        default="en_US-lessac-medium",
        help="piper voice model (name or .onnx path)",
    )
    ap.add_argument("--per-phrase", type=int, default=30, help="clips per phrase")
    args = ap.parse_args()

    out = Path(args.out).expanduser()
    positives = out / "positives"
    made = synthesize(positives, args.voice, args.per_phrase)
    print(f"\n✓ Wrote {made} 'Kai' clips to {positives}")
    print(
        "\nNext — train the model with openWakeWord (needs the [wake] extra and\n"
        "its training deps + a negatives/background set). Follow openWakeWord's\n"
        "automatic training guide, pointing its positives at the folder above:\n"
        "  https://github.com/dscripka/openWakeWord#training-new-models\n\n"
        f"Then set  [wake] model_path = \"{out}/kai.onnx\"  and enable [wake]."
    )


if __name__ == "__main__":
    main()
