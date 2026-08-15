"""Calibrate chars-per-token for this corpus's text shape.

Step 2 of 3. `pdftotext` preserves column layout, so ~62% of the extracted
text is whitespace padding — which tokenises very differently from prose. A
generic 4-chars-per-token rule is wrong here by enough to change the answer.

This reads the real corpus only to extract a LAYOUT SKELETON: the per-line
sequence of (word-length, gap-length) pairs. Every real word is then replaced
by a synthetic word of the same length from a generic engineering vocabulary,
so the text sent to `count_tokens` has the corpus's exact whitespace geometry
and token density but none of its content.

**No client document text is transmitted.** That property is the whole point
of the skeleton approach — keep it if you edit this file.

    python docs/pricing/calibrate_tokens.py [project-slug]

Needs ANTHROPIC_API_KEY (read from .env). `count_tokens` is not billed.
"""
import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv                     # noqa: E402
load_dotenv(ROOT / ".env", override=True)

from procurement.loaders import read_text          # noqa: E402

SLUG = sys.argv[1] if len(sys.argv) > 1 else "phase4c-shipped-defaults"
VENDORS = Path("projects") / SLUG / "vendors"
if not VENDORS.is_dir():
    sys.exit(f"no vendor documents at {VENDORS}")

random.seed(11)

VOCAB = ("Rated Output Power Voltage Frequency Speed Engine Model Generator "
         "Alternator Cooling Water Inlet Temperature Pressure Ambient Design "
         "Nominal Continuous Standby Prime Efficiency Fuel Gas Natural Phase "
         "Consumption Insulation Class Protection Enclosure Degree Standard "
         "Item Description Unit Quantity Remarks Vendor Compliance Note Yes No "
         "Complied Deviation Clause Section Attachment Datasheet Specification "
         "Type Series Rev Sheet Page Document Number Title Approved Checked "
         "Silencer Radiator Exhaust Manifold Turbocharger Bearing Lubrication "
         "Starting Battery Charger Control Panel Synchronizing Breaker Load "
         "kW kVA Hz rpm bar degC mm kg Nm3 kWh ISO IEC API mbar percent").split()
BY_LEN = {}
for w in VOCAB:
    BY_LEN.setdefault(len(w), []).append(w)
LENS = sorted(BY_LEN)


def word_of_len(n: int) -> str:
    """A synthetic token of exactly n characters."""
    if n in BY_LEN and random.random() < 0.55:
        return random.choice(BY_LEN[n])
    r = random.random()
    if r < 0.35:
        return "".join(random.choice("0123456789") for _ in range(n))
    if r < 0.60:
        return "".join(random.choice("ABCDEFGHIJKLMNOPRSTUVW") for _ in range(n))
    near = min(LENS, key=lambda k: abs(k - n))
    w = random.choice(BY_LEN[near])
    return (w + "-" + "X" * n)[:n] if len(w) < n else w[:n]


# ---- 1. skeletonise the real corpus (locally; nothing sent) ----------------
skeleton, real_chars, real_ws = [], 0, 0
for p in VENDORS.rglob("*"):
    if not p.is_file():
        continue
    try:
        t = read_text(str(p)) or ""
    except Exception:
        continue
    if not t:
        continue
    real_chars += len(t)
    real_ws += sum(c.isspace() for c in t)
    for line in t.splitlines():
        lead = len(line) - len(line.lstrip())
        row, i = [], lead
        while i < len(line):
            j = i
            while j < len(line) and not line[j].isspace():
                j += 1
            wlen, k = j - i, j
            while k < len(line) and line[k].isspace():
                k += 1
            if wlen:
                row.append((wlen, k - j))
            i = k if k > i else i + 1
        skeleton.append((lead, row))

if not skeleton:
    sys.exit("read no text from the corpus; is pdftotext on PATH?")

print(f"real corpus : {real_chars:,} chars, {len(skeleton):,} lines, "
      f"{100*real_ws/real_chars:.1f}% whitespace")

# ---- 2. refill the skeleton with synthetic words ---------------------------
random.shuffle(skeleton)
out, size = [], 0
for lead, row in skeleton:
    if size > 200_000:
        break
    line = " " * lead + "".join(word_of_len(wl) + " " * gl for wl, gl in row)
    out.append(line)
    size += len(line) + 1
synthetic = "\n".join(out)
sws = sum(c.isspace() for c in synthetic)
print(f"synthetic   : {len(synthetic):,} chars, {100*sws/len(synthetic):.1f}% whitespace")
print("  (the two whitespace shares must match, or the calibration is measuring "
      "a different text shape than the one you are billing for)")

key = os.getenv("ANTHROPIC_API_KEY")
if not key:
    sys.exit("\nno ANTHROPIC_API_KEY; cannot calibrate")

import anthropic                                    # noqa: E402
client = anthropic.Anthropic(api_key=key.strip('"').strip("'"))

results = {"real_whitespace_pct": round(100 * real_ws / real_chars, 1),
           "synthetic_whitespace_pct": round(100 * sws / len(synthetic), 1)}

for model in ("claude-sonnet-5", "claude-opus-5"):
    try:
        r = client.messages.count_tokens(
            model=model, messages=[{"role": "user", "content": synthetic}])
        cpt = len(synthetic) / r.input_tokens
        results[f"input_cpt_{model}"] = round(cpt, 2)
        print(f"  {model:18} {r.input_tokens:>9,} tok  ->  {cpt:.2f} chars/token")
    except Exception as exc:
        print(f"  {model}: {type(exc).__name__}: {exc}")

# ---- 3. output side: real JSON structure, synthetic values -----------------
facts = next((Path("projects") / SLUG / "store" / "vendors").rglob("*.json"), None)
if facts:
    def scrub(o):
        if isinstance(o, dict):
            return {k: scrub(v) for k, v in o.items()}
        if isinstance(o, list):
            return [scrub(v) for v in o]
        if isinstance(o, str):
            return " ".join(word_of_len(random.choice([4, 5, 6, 7]))
                            for _ in range(max(1, len(o) // 6)))
        return o

    sample = json.dumps(scrub(json.loads(facts.read_text(encoding="utf-8"))), indent=2)
    try:
        r = client.messages.count_tokens(
            model="claude-sonnet-5", messages=[{"role": "user", "content": sample}])
        cpt = len(sample) / r.input_tokens
        results["output_cpt"] = round(cpt, 2)
        print(f"  {'JSON output':18} {r.input_tokens:>9,} tok  ->  {cpt:.2f} chars/token")
    except Exception as exc:
        print(f"  json: {type(exc).__name__}: {exc}")

(Path(__file__).parent / "cpt.json").write_text(json.dumps(results, indent=2))
print(f"\nwrote {Path(__file__).parent / 'cpt.json'}")
