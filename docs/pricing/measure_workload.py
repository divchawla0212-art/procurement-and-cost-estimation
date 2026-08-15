"""Measure the real LLM workload of one ingested tender.

Step 1 of 3. Reads the same code paths production uses — `loaders.read_text`,
`VENDOR_ROUTE`, `chunk_on_lines` at the shipped budgets — so the call count and
the input character volume are what a real run would actually send, not an
estimate of it.

    python docs/pricing/measure_workload.py [project-slug]

Writes `measured.json` beside this script; `estimate_cost.py` reads it.

The corpus is an untracked ingested project under `projects/`, so this script
only runs on a workstation that has one. That is deliberate: the numbers are
measured against a real multi-vendor tender or they are not measured at all.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from procurement.loaders import read_text                      # noqa: E402
from procurement.chunking import chunk_on_lines                # noqa: E402
from procurement.extract_tech import TECH_CHUNK_CHARS          # noqa: E402
from procurement.extract_requirements import REQUIREMENTS_CHUNK_CHARS  # noqa: E402
from procurement.pipeline import VENDOR_ROUTE, SECONDARY_VENDOR_ROUTE  # noqa: E402

SLUG = sys.argv[1] if len(sys.argv) > 1 else "phase4c-shipped-defaults"
PROJ = Path("projects") / SLUG
PROMPTS = Path("shared/llm/prompts")

if not (PROJ / "store" / "documents.json").exists():
    sys.exit(f"no ingested project at {PROJ}. Pass a slug from projects/, or "
             f"ingest a multi-vendor tender first.")

PROMPT_CHARS = {
    "quotation":    len((PROMPTS / "bid_extract_v1.txt").read_text(encoding="utf-8")),
    "datasheet":    len((PROMPTS / "tech_facts_v1.txt").read_text(encoding="utf-8")),
    "deviation":    len((PROMPTS / "deviation_v1.txt").read_text(encoding="utf-8")),
    "requirements": len((PROMPTS / "requirements_v4.txt").read_text(encoding="utf-8")),
    "mom":          len((PROMPTS / "mom_amend_v1.txt").read_text(encoding="utf-8")),
    "classify":     len((PROMPTS / "doc_class_v1.txt").read_text(encoding="utf-8")),
}
CHUNKED = {"datasheet": TECH_CHUNK_CHARS, "requirements": REQUIREMENTS_CHUNK_CHARS}

# classify_document sends the filename plus this many chars of head text
CLASSIFY_HEAD_CHARS = 500

recs = json.loads((PROJ / "store" / "documents.json").read_text(encoding="utf-8"))

calls, doc_rows, unread = [], [], []

for r in recs:
    path = r.get("path")
    doc_class = r.get("doc_class") or "other"
    vendor = r.get("vendor")

    # Classification is rule-first; only the documents the rules declined
    # ever reached the model, and then on a 500-char head.
    if r.get("classified_by") == "llm":
        calls.append(("classify", PROMPT_CHARS["classify"] + CLASSIFY_HEAD_CHARS + 60))

    if not path:
        continue
    full = Path(path) if Path(path).exists() else PROJ / path
    if not full.exists():
        found = next((p for p in (PROJ / "vendors").rglob(Path(path).name)), None)
        if found is None:
            unread.append(path)
            continue
        full = found
    try:
        text = read_text(str(full)) or ""
    except Exception as exc:
        unread.append(f"{path}: {exc}")
        continue

    if vendor:
        routes = []
        primary = VENDOR_ROUTE.get(doc_class)
        if primary:
            routes.append(primary)
            secondary = SECONDARY_VENDOR_ROUTE.get(primary)
            if secondary:
                routes.append(secondary)
    else:
        # the client's own side of the tender routes by its own table
        routes = ["requirements" if doc_class in ("spec", "datasheet") else "mom"]

    n_for_doc = 0
    for route in routes:
        prompt = PROMPT_CHARS.get(route, PROMPT_CHARS["datasheet"])
        if route in CHUNKED:
            for chunk in chunk_on_lines(text, CHUNKED[route]):
                calls.append((route, prompt + len(chunk) + 200))
                n_for_doc += 1
        else:
            calls.append((route, prompt + len(text) + 200))
            n_for_doc += 1

    doc_rows.append({"vendor": vendor, "doc_class": doc_class,
                     "file": full.name, "chars": len(text),
                     "routes": routes, "calls": n_for_doc})

# ---- output side: what the run actually stored -----------------------------
stored_compact = 0
vend_dir = PROJ / "store" / "vendors"
if vend_dir.is_dir():
    for f in vend_dir.rglob("*.json"):
        stored_compact += len(json.dumps(json.loads(f.read_text(encoding="utf-8")),
                                         separators=(",", ":")))
req = PROJ / "store" / "requirements.json"
if req.exists():
    stored_compact += len(json.dumps(json.loads(req.read_text(encoding="utf-8")),
                                     separators=(",", ":")))

# ---- whitespace share: pdftotext preserves column layout -------------------
total_text = sum(d["chars"] for d in doc_rows)

by_route = {}
for route, chars in calls:
    n, c = by_route.get(route, (0, 0))
    by_route[route] = (n + 1, c + chars)

print(f"{'vendor':10} {'class':10} {'chars':>9} {'routes':24} {'calls':>5}  file")
for d in sorted(doc_rows, key=lambda x: -x["chars"]):
    print(f"{str(d['vendor']):10} {d['doc_class']:10} {d['chars']:9,} "
          f"{','.join(d['routes']):24} {d['calls']:5}  {d['file'][:42]}")

print()
print(f"documents read        : {len(doc_rows)}")
print(f"vendors               : {len({d['vendor'] for d in doc_rows if d['vendor']})}")
print(f"extracted text chars  : {total_text:,}")
print(f"LLM calls per ingest  : {len(calls)}")
print(f"input chars sent      : {sum(c for _, c in calls):,}")
print(f"stored output (compact JSON) : {stored_compact:,} chars")
if unread:
    print(f"UNREAD ({len(unread)}): {unread[:5]}")

print()
print(f"{'route':14} {'calls':>6} {'input chars':>14}")
for route, (n, c) in sorted(by_route.items(), key=lambda x: -x[1][1]):
    print(f"{route:14} {n:6} {c:14,}")

out = {
    "project": SLUG,
    "docs": len(doc_rows),
    "vendors": len({d["vendor"] for d in doc_rows if d["vendor"]}),
    "llm_calls": len(calls),
    "input_chars": sum(c for _, c in calls),
    "text_chars": total_text,
    "stored_output_chars": stored_compact,
    "by_route": {k: {"calls": v[0], "input_chars": v[1]} for k, v in by_route.items()},
}
(Path(__file__).parent / "measured.json").write_text(json.dumps(out, indent=2))
print(f"\nwrote {Path(__file__).parent / 'measured.json'}")
