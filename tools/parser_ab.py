"""Run the PDF parser A/B and report review-flag counts per arm.

    python -m tools.parser_ab                  # all arms
    python -m tools.parser_ab --arms pypdf pdftotext

Design: docs/superpowers/specs/2026-08-11-pdf-parser-quality-ab-design.md

Needs a provider key, so it is a script and never a test -- CLAUDE.md requires
the suite to stay key-free. The deterministic half of the experiment lives in
tests/test_datasheet_row_recall.py and does run in CI.
"""
import argparse
import json
import os
import shutil
import sys

import dotenv

from procurement.compliance import evaluate_project
from procurement.coverage import build_extraction_status
from procurement.loaders import read_text
from procurement.matrix import build_matrix
from procurement.pipeline import run_ingestion
from procurement.project import (create_project, list_vendor_dirs,
                                 load_project, save_project)
from procurement.store import snapshots
from shared.llm.factory import get_client
from tools.datasheet_ground_truth import (content_length, parameters, recall,
                                          recall_unordered)

ARMS = ("pdftotext", "pypdf", "llamaparse")

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CORPUS = os.path.join(_HERE, "processed-data")

# The client document the requirement set is extracted from, once, and shared
# byte-for-byte by every arm.
RFQ_DOCUMENTS = [
    os.path.join(_CORPUS, "01-client-mr-rfq",
                 "ADN-AEC-ME-SPC-026 MR Gas Genset copy.pdf"),
]

# Three vendors. KERUI and ADPOWER are PDFs and move with the arm; MKON is the
# .xlsx control, read by openpyxl in every arm.
VENDOR_DOCUMENTS = {
    "KERUI": [os.path.join(
        _CORPUS, "02-vendor-bids", "KERUI",
        "01 DOD-30201-50150-BH-000-16-00-004 DataSheet Gas Generator.pdf")],
    "ADPOWER": [os.path.join(
        _CORPUS, "02-vendor-bids", "ADPOWER",
        "ADN-AEC-ME-SPC-026 MR Gas Genset copy.pdf")],
    "MKON": [os.path.join(
        _CORPUS, "01-client-mr-rfq",
        "DOD-30201-50150-BH-000-16-00-004 DataSheet Gas Generator.xlsx")],
}

CONTROL_VENDOR = "MKON"
GROUND_TRUTH_XLSX = VENDOR_DOCUMENTS["MKON"][0]
SCORED_PDF = VENDOR_DOCUMENTS["KERUI"][0]

TEMPLATE_SLUG = "parser-ab-template"


def _copy(src: str, dest_dir: str) -> None:
    os.makedirs(dest_dir, exist_ok=True)
    shutil.copy2(src, os.path.join(dest_dir, os.path.basename(src)))


def build_template(root: str, client) -> str:
    """A project holding the extracted requirement set and no vendors.

    Built once and copied per arm, so every arm scores against a byte-identical
    requirements.json. Compliance is counted per requirement x vendor, so the
    requirement set is the denominator of the metric; re-extracting it per arm
    would let the denominator move and make the arms incomparable.
    """
    pdir = os.path.join(root, TEMPLATE_SLUG)
    if os.path.exists(os.path.join(pdir, "store", "requirements.json")):
        print(f"  template: reusing {pdir}")
        return TEMPLATE_SLUG

    create_project(root, TEMPLATE_SLUG)
    for doc in RFQ_DOCUMENTS:
        _copy(doc, os.path.join(pdir, "requirements"))
    print(f"  template: extracting requirements from "
          f"{len(RFQ_DOCUMENTS)} client document(s)...")
    run_ingestion(root, TEMPLATE_SLUG, client)
    return TEMPLATE_SLUG


def prepare_arm(root: str, arm: str) -> str:
    """A fresh project for one arm: the template plus the three vendors."""
    slug = f"parser-ab-{arm}"
    pdir = os.path.join(root, slug)
    if os.path.exists(pdir):
        shutil.rmtree(pdir)
    shutil.copytree(os.path.join(root, TEMPLATE_SLUG), pdir)

    for vendor, docs in VENDOR_DOCUMENTS.items():
        for doc in docs:
            _copy(doc, os.path.join(pdir, "vendors", vendor))

    project = load_project(root, slug)
    project.slug = slug
    project.vendors = list_vendor_dirs(root, slug)
    save_project(root, project)
    return slug


def _flag_counts(root: str, slug: str) -> dict:
    """`needs_human` cells, split by verdict and by vendor.

    This is the metric. matrix.py buckets `review` and `unanswered` as
    needs_human -- the worklist a person has to clear before an award can be
    defended -- so it is the only unit in the pipeline that maps to money.
    """
    matrix = build_matrix(root, slug)
    total = {"needs_human": 0, "review": 0, "unanswered": 0,
             "matched": 0, "not_matched": 0}
    by_vendor: dict[str, dict] = {
        v: {"needs_human": 0, "review": 0, "unanswered": 0}
        for v in matrix.vendors}
    for row in matrix.rows:
        for vendor, cell in row.cells.items():
            total[cell.group] = total.get(cell.group, 0) + 1
            if cell.group == "needs_human":
                by_vendor[vendor]["needs_human"] += 1
                by_vendor[vendor][cell.verdict] = (
                    by_vendor[vendor].get(cell.verdict, 0) + 1)
                total[cell.verdict] = total.get(cell.verdict, 0) + 1
    return {"total": total, "by_vendor": by_vendor,
            "requirements": len(matrix.rows), "vendors": list(matrix.vendors)}


def _row_recall(arm: str) -> dict:
    """The LLM-free half: how much of the ground-truth form this arm recovered."""
    names, _dropped = parameters(GROUND_TRUTH_XLSX)
    text = read_text(SCORED_PDF, llm_fallback=None, pdf_reader=arm)
    contiguous, _ = recall(text, names)
    unordered, _ = recall_unordered(text, names)
    return {"parameters": len(names),
            "contiguous": len(contiguous),
            "unordered": len(unordered),
            "raw_chars": len(text),
            # The only count comparable across arms -- see content_length.
            "content_chars": content_length(text)}


def run_arm(root: str, arm: str, client) -> dict:
    print(f"\n=== arm: {arm} ===")
    slug = prepare_arm(root, arm)
    run_ingestion(root, slug, client, pdf_reader=arm)
    evaluate_project(root, slug)

    status = build_extraction_status(root, slug)
    result = {
        "arm": arm,
        "flags": _flag_counts(root, slug),
        "recall": _row_recall(arm),
        "extraction": status.totals,
        "documents": [
            {"vendor": v.vendor, "path": d.path, "status": d.status,
             "text_source": d.text_source, "facts": d.fact_count,
             "notes": d.notes}
            for v in status.vendors for d in v.documents],
    }
    flags = result["flags"]["total"]
    print(f"  needs_human={flags['needs_human']} "
          f"(review={flags.get('review', 0)}, "
          f"unanswered={flags.get('unanswered', 0)})  "
          f"facts={status.totals.get('facts')}")
    return result


def _report(results: list[dict], control_drift: bool) -> str:
    out = ["", "=" * 72, "PDF PARSER A/B -- review flags per arm", "=" * 72, ""]
    header = (f"{'arm':<12}{'needs_human':>12}{'review':>9}{'unanswered':>12}"
              f"{'facts':>8}{'doc fails':>11}")
    out += [header, "-" * len(header)]
    for r in results:
        t = r["flags"]["total"]
        out.append(f"{r['arm']:<12}{t['needs_human']:>12}{t.get('review', 0):>9}"
                   f"{t.get('unanswered', 0):>12}"
                   f"{r['extraction'].get('facts', 0):>8}"
                   f"{r['extraction'].get('failed', 0):>11}")

    # An arm that extracted nothing did not "produce no review flags", it
    # failed. Say so where the numbers are, not only in a log line above.
    broken = [r["arm"] for r in results
              if r["extraction"].get("facts", 0) == 0]
    if broken:
        out += ["", f"!! {', '.join(broken)} extracted ZERO facts. Those rows "
                    f"are extraction failures, not clean results."]

    out += ["", f"needs_human by vendor ({CONTROL_VENDOR} is the control -- "
                f"its reader never changes)", ""]
    vendors = results[0]["flags"]["vendors"] if results else []
    head = f"{'arm':<12}" + "".join(f"{v:>12}" for v in vendors)
    out += [head, "-" * len(head)]
    for r in results:
        row = f"{r['arm']:<12}"
        for v in vendors:
            row += f"{r['flags']['by_vendor'][v]['needs_human']:>12}"
        out.append(row)

    if len(results) < 2:
        out += ["", "only one arm completed, so the control says nothing: a "
                    "single value cannot drift. Treat these numbers as "
                    "unvalidated."]
    elif control_drift:
        out += ["", "!! THE CONTROL DRIFTED. " + CONTROL_VENDOR + " is read by "
                "openpyxl in every arm, so its needs_human count must be",
                "   identical across arms. It is not, which means run-to-run "
                "model variance is at least",
                "   as large as the drift shown -- no smaller difference "
                "between arms is evidence of anything."]
    else:
        out += ["", f"control holds: {CONTROL_VENDOR} scored identically in "
                    f"every arm, so differences above are not sampling noise."]

    out += ["", "row recall against the ground-truth datasheet (no LLM)", ""]
    head = (f"{'arm':<12}{'contiguous':>12}{'all words':>12}{'content':>10}"
            f"{'raw':>10}")
    out += [head, "-" * len(head)]
    for r in results:
        rc = r["recall"]
        out.append(f"{r['arm']:<12}{rc['contiguous']:>7}/{rc['parameters']:<4}"
                   f"{rc['unordered']:>7}/{rc['parameters']:<4}"
                   f"{rc['content_chars']:>10}{rc['raw_chars']:>10}")
    out += ["", "  'content' = alphanumerics with markup stripped, the only "
                "count comparable across arms.",
            "  'raw' rewards pdftotext's -layout padding and LlamaParse's HTML "
            "table tags; shown to make that visible."]
    return "\n".join(out)


def main(argv=None) -> int:
    dotenv.load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arms", nargs="+", default=list(ARMS), choices=ARMS)
    parser.add_argument("--root", default="projects")
    parser.add_argument("--out", default="parser-ab-results.json")
    args = parser.parse_args(argv)

    # Deliberately NOT pinning LLM_TEMPERATURE. The design called for
    # temperature=0 to suppress sampling drift, and the first run of this
    # harness did exactly that and failed every single extraction with
    #   400 invalid_request_error: `temperature` is deprecated for this model
    # on claude-sonnet-5. The whole run reported needs_human=0 for every arm,
    # which reads as a perfect result and was in fact a total failure.
    #
    # So that control is unavailable on Claude 5. The env var still works for
    # providers and models that accept it, and is left for them; here the
    # defence against reading noise as signal is the MKON control alone, which
    # is the reason it is in the corpus.
    provider = os.getenv("LLM_PROVIDER", "mock")
    if provider == "mock":
        print("LLM_PROVIDER is 'mock' -- the arms would all score identically. "
              "Set a real provider in .env.", file=sys.stderr)
        return 2
    print(f"provider={provider} model={os.getenv('LLM_MODEL') or 'default'} "
          f"temperature={os.getenv('LLM_TEMPERATURE') or 'unset (not sent)'}")

    client = get_client()
    os.makedirs(args.root, exist_ok=True)
    build_template(args.root, client)

    # A run whose requirement extraction failed reports needs_human=0 for every
    # arm, which reads as a flawless result and is the opposite of one. The
    # first run of this harness did exactly that. Refuse rather than report.
    requirements = len(snapshots.load_requirements(
        args.root, TEMPLATE_SLUG).requirements)
    if requirements == 0:
        print("the template extracted ZERO requirements, so every arm would "
              "score needs_human=0 for lack of any cell to score. Refusing to "
              "report. Check the requirement extraction above for errors.",
              file=sys.stderr)
        return 3
    print(f"  template: {requirements} requirements (the denominator, shared "
          f"by every arm)")

    results, failures = [], {}
    for arm in args.arms:
        try:
            results.append(run_arm(args.root, arm, client))
        except Exception as exc:                # one arm must not kill the rest
            failures[arm] = f"{type(exc).__name__}: {exc}"
            print(f"  ARM FAILED: {failures[arm]}", file=sys.stderr)

    control = {r["flags"]["by_vendor"][CONTROL_VENDOR]["needs_human"]
               for r in results}
    drift = len(results) > 1 and len(control) > 1

    report = _report(results, drift) if results else "no arm completed"
    if failures:
        report += "\n\nfailed arms:\n" + "\n".join(
            f"  {a}: {m}" for a, m in failures.items())
    print(report)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"results": results, "failures": failures,
                   "control_drift": drift,
                   "provider": provider,
                   "model": os.getenv("LLM_MODEL"),
                   "temperature": os.getenv("LLM_TEMPERATURE")}, fh, indent=2)
    print(f"\nwrote {args.out}")
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
