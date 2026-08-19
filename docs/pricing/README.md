# Pricing — cost basis and tiers

Two pages and the four scripts that produced every number in them.

| File | What it is |
|---|---|
| [`cost-basis.html`](cost-basis.html) | What it costs to serve one tender, one credit, one seat. No margin. |
| [`pricing-tiers.html`](pricing-tiers.html) | Three plans with margin applied, the per-seat overage, credit packs, sensitivity. |
| [`consumption-model.md`](consumption-model.md) | Megabytes to credits to dollars, with every constant and its derivation. |

Open either in a browser — they are self-contained and need no server.

## Pitching it

| File | Audience |
|---|---|
| [`pitch-deck-with-pricing.pptx`](pitch-deck-with-pricing.pptx) | Client. The 16-slide deck with three pricing slides added after slide 12. |
| [`client-one-pager.html`](client-one-pager.html) | Client. The pilot offer as a leave-behind. |
| [`talk-track.html`](talk-track.html) | **Internal only.** Sequence, objection handling, the floor and the walk-away. |

The strategy is **pilot at a fixed fee, then value-price the renewal**: $7,500 for
90 days credited back in full, then an annual plan sized from what the pilot
actually measured. The pilot price is set to sit inside a manager's signing
authority, not to recover cost — check the client's delegated-authority
threshold before quoting it.

`talk-track.html` carries three figures that must not reach a client, the
cost-plus tier among them. Read its red box before using any of this.

## The scripts

Run in order. Steps 1 and 2 measure; step 3 and the tier model are arithmetic
on top of what they recorded.

```bash
python docs/pricing/measure_workload.py [project-slug]   # 1. workload, from a real corpus
python docs/pricing/calibrate_tokens.py [project-slug]   # 2. chars-per-token for this text shape
python docs/pricing/estimate_cost.py                     # 3. cost basis + credit unit
python docs/pricing/price_tiers.py                       # 4. margin, plans, packs, sensitivity
```

**Steps 1 and 2 do not run on this branch.** They import `procurement.chunking`,
`VENDOR_ROUTE`, `SECONDARY_VENDOR_ROUTE`, `TECH_CHUNK_CHARS` and
`REQUIREMENTS_CHUNK_CHARS`, none of which exist here yet, and they need an
ingested multi-vendor tender under `projects/`, which is untracked. Run them
from a checkout that has both — they were originally measured on
`rfq-platform-phase-1` against `projects/phase4c-shipped-defaults`.

Steps 3 and 4 run anywhere. With no `measured.json` / `cpt.json` beside them
they fall back to the figures recorded on 2026-08-14 — the ones the two HTML
pages were built from — and print which source they used.

## Two things that will go stale

- **Model prices.** `PRICES` in `estimate_cost.py` is a hand-maintained copy of
  Anthropic's first-party list rates. Check them before quoting anything.
- **The corpus.** `measure_workload.py` reports whichever project you point it
  at. A tender with more vendors or more scanned drawings moves the numbers.

Re-running the measurement can shift chars-per-token slightly (the layout
skeleton samples a different set of lines), which is why the generated
`measured.json` and `cpt.json` are not committed — regenerate them rather than
trusting a stale copy.

## One constraint worth knowing

`calibrate_tokens.py` sends **synthetic** text to `count_tokens`, never client
documents. It reads the real corpus only to extract a layout skeleton — the
per-line pattern of word and gap lengths — then refills that skeleton with
words from a generic engineering vocabulary. The whitespace share of the
synthetic text must match the real one, or the calibration is measuring a
different text shape than the one being billed for; the script prints both so
you can check. Keep that property if you edit it.

## The caveat both pages carry

Every figure is cost-plus. That yields 70–84% gross margins and a model that
cannot lose money — and it prices a tender comparison at about 1% of the
engineer time it replaces. Use these as the floor and the internal margin
model, not as a list price.

Nothing here can be billed yet: no token usage is recorded anywhere in
`shared/llm/` or `procurement/`, so the meter these plans depend on does not
exist.
