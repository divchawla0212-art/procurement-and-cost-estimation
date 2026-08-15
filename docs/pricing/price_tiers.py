"""Pricing tiers built on the measured cost basis.

Applies margin to the output of estimate_cost.py and prints the three plans,
the per-seat overage, the top-up packs and the margin sensitivity that the
`pricing-tiers.html` page renders.

    python docs/pricing/price_tiers.py

Every number here is cost-plus. See the closing VALUE CHECK: it prices a
tender at roughly 1% of the labour it replaces, which means price is
constrained by what a buyer will agree to, not by what compute costs. Treat
this as the floor and the internal margin model, not as a list price.
"""

# ---- from estimate_cost.py, recorded 2026-08-14 ---------------------------
TOKENS_PER_TENDER = 472_786
BLENDED_PER_MTOK = 6.09        # Sonnet 5 at this workload's 74/26 input/output mix
INFRA_MO_10_SEATS = (57, 150)

# ---- decision 1: the credit unit ------------------------------------------
TOKENS_PER_CREDIT = 5_000      # chosen so one tender lands on ~100 credits
RAW_CREDIT = BLENDED_PER_MTOK * TOKENS_PER_CREDIT / 1e6
TENDER_CREDITS = TOKENS_PER_TENDER / TOKENS_PER_CREDIT


def rule(ch="="):
    print(ch * 78)


rule()
print("CREDIT UNIT")
rule()
print(f"  1 credit          = {TOKENS_PER_CREDIT:,} tokens Sonnet-5-equivalent")
print(f"  raw cost          = ${RAW_CREDIT:.4f}")
print(f"  one tender ingest = {TENDER_CREDITS:.0f} credits (${TENDER_CREDITS*RAW_CREDIT:.2f} raw)")

# ---- decision 2: the credit price ladder ----------------------------------
LADDER = [("Pay-as-you-go top-up", 0.150), ("Starter bundle", 0.125),
          ("Growth bundle", 0.105), ("Scale bundle", 0.090)]
rule()
print("CREDIT PRICE LADDER")
rule()
print(f"{'band':28} {'$/credit':>10} {'markup':>8} {'GM %':>7} {'$/tender':>10}")
rule("-")
for name, p in LADDER:
    print(f"{name:28} {'$%.3f' % p:>10} {'%.1fx' % (p/RAW_CREDIT):>8} "
          f"{'%.1f%%' % ((p-RAW_CREDIT)/p*100):>7} {'$%.2f' % (p*TENDER_CREDITS):>10}")

# ---- decision 3: seats carry the margin -----------------------------------
EXTRA_SEAT = 75                # per month, beyond the plan's included seats
PLANS = [
    # name,     seats, $/seat/mo, credits/mo, $/credit, infra (lo, hi) $/mo
    ("Starter", 10, 50, 2_000, 0.125, (57, 150)),
    ("Growth",  25, 45, 6_000, 0.105, (120, 300)),
    ("Scale",   50, 40, 15_000, 0.090, (250, 550)),
]

rule()
print("PLAN TIERS (annual)")
rule()
hdr = (f"{'plan':9} {'seats':>5} {'platform/yr':>12} {'credits/yr':>11} "
       f"{'credit rev':>11} {'TOTAL/yr':>10} {'COGS/yr':>16} {'GM':>8}")
print(hdr)
rule("-")
rows = []
for name, seats, seat_mo, cr_mo, cr_p, (ilo, ihi) in PLANS:
    platform, cr_yr = seats * seat_mo * 12, cr_mo * 12
    cr_rev = cr_yr * cr_p
    total = platform + cr_rev
    cogs_lo, cogs_hi = cr_yr*RAW_CREDIT + ilo*12, cr_yr*RAW_CREDIT + ihi*12
    rows.append((name, seats, cr_yr, total, cogs_lo, cogs_hi))
    print(f"{name:9} {seats:>5} {'$%d' % platform:>12} {cr_yr:>11,} "
          f"{'$%d' % cr_rev:>11} {'$%d' % total:>10} "
          f"{'$%d-%d' % (cogs_lo, cogs_hi):>16} "
          f"{'%.0f-%.0f%%' % ((total-cogs_hi)/total*100, (total-cogs_lo)/total*100):>8}")

print()
print("  tenders included (at ~100 credits each):")
for name, seats, cr_yr, *_ in rows:
    print(f"    {name:9} {cr_yr/TENDER_CREDITS:>6,.0f}/yr = "
          f"{cr_yr/TENDER_CREDITS/12/seats:.1f} per seat per month")

rule()
print("THE 11th SEAT AND BEYOND")
rule()
print(f"  price                 ${EXTRA_SEAT}/seat/month (${EXTRA_SEAT*12}/yr)")
print(f"  marginal cost         ~$2/month infrastructure")
print(f"  gross margin          ~{(EXTRA_SEAT-2)/EXTRA_SEAT*100:.0f}% on the seat itself")
print(f"  credits included      none - drawn from the account's shared pool")
print()
print(f"  at 4 tenders/mo that seat burns {4*TENDER_CREDITS:.0f} credits: "
      f"${4*TENDER_CREDITS*RAW_CREDIT:.2f} to serve, ${4*TENDER_CREDITS*0.125:.2f} billed")
print()
print("  Deliberately above the $50/seat inside Starter, so buying the next plan")
print("  up is always cheaper per seat than bolting seats onto the current one.")

rule()
print("TOP-UP PACKS")
rule()
print(f"{'pack':>10} {'price':>9} {'$/credit':>10} {'tenders':>9} {'GM':>7}")
rule("-")
for n, p in ((1_000, 0.15), (5_000, 0.14), (20_000, 0.13), (50_000, 0.12)):
    print(f"{n:>10,} {'$%d' % (n*p):>9} {'$%.2f' % p:>10} "
          f"{n/TENDER_CREDITS:>9.0f} {'%.0f%%' % ((p-RAW_CREDIT)/p*100):>7}")

rule()
print("MARGIN SENSITIVITY — Starter, $9,000/yr")
rule()
_, _, cr_yr, total, *_ = rows[0]
print(f"{'scenario':46} {'COGS/yr':>12} {'GM':>8}")
rule("-")
for label, use, mult in (
        ("Adopt Batch API (x0.50 per credit)", 1.00, 0.50),
        ("Only 60% of credits used", 0.60, 1.00),
        ("Opus 5 + Batch API", 1.00, 0.835),
        ("Sonnet 5, credits fully used (base case)", 1.00, 1.00),
        ("Switch to Opus 5 (x1.67 per credit)", 1.00, 1.67)):
    c = cr_yr * use * RAW_CREDIT * mult + 100 * 12      # mid infra
    print(f"{label:46} {'$%d' % c:>12} {'%.0f%%' % ((total-c)/total*100):>8}")

rule()
print("VALUE CHECK — read this before quoting any of the above")
rule()
for hrs, rate in ((16, 45), (24, 60), (40, 75)):
    manual, price = hrs * rate, TENDER_CREDITS * 0.125
    print(f"  {hrs:>2}h of engineer time @ ${rate}/h = ${manual:>5,}  "
          f"vs ${price:.2f} charged  ->  {manual/price:>5.0f}x")
print()
print("  A 61-254x gap is not assumption error. It says the binding constraint")
print("  on price is what a buyer will agree to, not what the compute costs.")
print("  Of the two levers, the seat price has far more headroom than the credit")
print("  rate — marginal cost is ~$2/seat/month either way.")
