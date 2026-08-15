"""Turn the measured workload into a cost basis and a credit unit.

Step 3 of 3. Reads `measured.json` (from measure_workload.py) and `cpt.json`
(from calibrate_tokens.py) when they are present, and falls back to the values
recorded on 2026-08-14 otherwise, so the script still runs on a checkout with
no ingested corpus — it just reports the recorded figures rather than fresh
ones. It says which it used.

    python docs/pricing/estimate_cost.py

MODEL PRICES GO STALE. `PRICES` below is a hand-maintained copy of Anthropic's
first-party list rates. Check them before quoting anything from this output.
"""
import json
from pathlib import Path

HERE = Path(__file__).parent

# ---- recorded 2026-08-14 on projects/phase4c-shipped-defaults --------------
RECORDED = {
    "docs": 37, "vendors": 4, "llm_calls": 102,
    "input_chars": 1_197_552, "text_chars": 818_229,
    "stored_output_chars": 217_819,
}
RECORDED_CPT = {"input_cpt_claude-sonnet-5": 3.41, "output_cpt": 2.06}


def load(name, fallback):
    p = HERE / name
    if p.exists():
        try:
            return json.loads(p.read_text()), True
        except Exception:
            pass
    return fallback, False


m, m_fresh = load("measured.json", RECORDED)
c, c_fresh = load("cpt.json", RECORDED_CPT)

CPT_IN = c.get("input_cpt_claude-sonnet-5") or RECORDED_CPT["input_cpt_claude-sonnet-5"]
CPT_OUT = c.get("output_cpt") or RECORDED_CPT["output_cpt"]

# Raw model output exceeds what the merge stores: `ask_each_chunk` uses
# items.extend with no de-duplication, so stored output is close to what was
# emitted — this allowance covers empty/failed calls and the tool-call envelope.
OUTPUT_ALLOWANCE = 1.15

TOK_IN = m["input_chars"] / CPT_IN
TOK_OUT = m["stored_output_chars"] * OUTPUT_ALLOWANCE / CPT_OUT

PRICES = {                       # $ per million tokens (input, output)
    "Haiku 4.5":                        (1.00, 5.00),
    "Sonnet 5 (intro, to 31 Aug 2026)": (2.00, 10.00),
    "Sonnet 5  <- current default":     (3.00, 15.00),
    "Opus 5":                           (5.00, 25.00),
}
BATCH = 0.50
REFERENCE = "Sonnet 5  <- current default"


def cost(pin, pout):
    return TOK_IN * pin / 1e6 + TOK_OUT * pout / 1e6


def rule(ch="="):
    print(ch * 78)


rule()
print("ONE TENDER INGEST — measured workload")
rule()
print(f"  source                   {'measured.json' if m_fresh else 'recorded 2026-08-14'}"
      f" / {'cpt.json' if c_fresh else 'recorded chars-per-token'}")
print(f"  documents / vendors      {m['docs']} / {m['vendors']}")
print(f"  LLM calls                {m['llm_calls']}")
print(f"  input tokens             {TOK_IN:>11,.0f}   ({m['input_chars']:,} chars @ {CPT_IN} c/tok)")
print(f"  output tokens            {TOK_OUT:>11,.0f}   ({m['stored_output_chars']:,} chars @ {CPT_OUT} c/tok)")
print(f"  total tokens             {TOK_IN + TOK_OUT:>11,.0f}")
print()
print(f"{'model':36} {'per tender':>12} {'w/ Batch API':>14}")
rule("-")
per_tender = {}
for name, (pi, po) in PRICES.items():
    per_tender[name] = cost(pi, po)
    print(f"{name:36} {'$%.2f' % per_tender[name]:>12} "
          f"{'$%.2f' % (per_tender[name] * BATCH):>14}")

rule()
print("CREDIT UNIT")
rule()
share_in = TOK_IN / (TOK_IN + TOK_OUT)
ref_in, ref_out = PRICES[REFERENCE]
blended = ref_in * share_in + ref_out * (1 - share_in)
print(f"  token mix                {share_in*100:.0f}% input / {(1-share_in)*100:.0f}% output")
print(f"  blended reference rate   ${blended:.2f} per million tokens ({REFERENCE.split('  <-')[0]})")
print()
for per_credit_tokens in (1_000, 5_000):
    raw = blended * per_credit_tokens / 1e6
    n = (TOK_IN + TOK_OUT) / per_credit_tokens
    print(f"  1 credit = {per_credit_tokens:>6,} tokens  ->  ${raw:.4f} raw, "
          f"one tender = {n:,.0f} credits")
print()
print("  (the 5,000-token unit is the one the published tiers use: it puts one")
print("   tender at ~100 credits, which a buyer can hold in their head)")

print()
print("  model multiplier, blended at this workload's mix:")
for name, (pi, po) in PRICES.items():
    b = pi * share_in + po * (1 - share_in)
    print(f"    {name:36} x{b/blended:.2f}")

rule()
print("10-SEAT BUNDLE — monthly inference cost")
rule()
tender_credits_5k = (TOK_IN + TOK_OUT) / 5_000
print(f"{'tenders/user/mo':>16} {'tenders/mo':>11} {'credits/mo':>12} "
      f"{'Haiku':>9} {'Sonnet 5':>10} {'Opus 5':>9}")
rule("-")
for pu in (1, 2, 4, 8):
    n = pu * 10
    print(f"{pu:>16} {n:>11} {n*tender_credits_5k:>12,.0f} "
          f"{'$%.0f' % (per_tender['Haiku 4.5']*n):>9} "
          f"{'$%.0f' % (per_tender[REFERENCE]*n):>10} "
          f"{'$%.0f' % (per_tender['Opus 5']*n):>9}")

rule()
print("NON-AI RUNNING COST (10 seats) — estimated, not measured")
rule()
infra = [("App server (FastAPI + built SPA, 2 vCPU / 4 GB)", 40, 90),
         ("Object storage + backups (~45 MB per tender)", 5, 20),
         ("Domain, TLS, log/uptime monitoring", 10, 30),
         ("Egress", 2, 10)]
for name, a, b in infra:
    print(f"  {name:52} ${a:>3} - ${b:<4}/mo")
lo, hi = sum(a for _, a, _ in infra), sum(b for _, _, b in infra)
rule("-")
print(f"  {'TOTAL infrastructure':52} ${lo:>3} - ${hi:<4}/mo  (${lo*12}-${hi*12}/yr)")
print()
print("  No managed DB, queue or vector store: the registry is SQLite and the")
print("  project stores are JSON snapshots on disk. No paid parsing service —")
print("  pdftotext/pypdf are local. LlamaParse is in the tree but production")
print("  passes pdf_reader=None and never reaches it.")

rule()
print("MARGINAL SEAT — cost only, no margin")
rule()
print(f"{'usage':>22} {'Haiku':>10} {'Sonnet 5':>11} {'Opus 5':>10}")
rule("-")
for pu in (1, 2, 4):
    print(f"{str(pu) + ' tender/mo':>22} "
          f"{'$%.2f' % (per_tender['Haiku 4.5']*pu):>10} "
          f"{'$%.2f' % (per_tender[REFERENCE]*pu):>11} "
          f"{'$%.2f' % (per_tender['Opus 5']*pu):>10}")
print()
print("  Infrastructure adds ~$1-3 per seat per month and is near-flat until the")
print("  app server saturates, so a seat's cost is almost entirely the tenders it")
print("  runs. That is the argument for the credit model: the seat is nearly")
print("  free, the work is not.")
