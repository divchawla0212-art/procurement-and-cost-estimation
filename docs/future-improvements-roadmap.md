# Future Improvements & Roadmap

**Purpose:** a living backlog of everything intentionally deferred, so future development has a single reference. Nothing here blocks the current 4-day Procurement Comparison Portal MVP — this is what comes *after*.

**Last updated:** 2026-07-28
**Legend:** 🟥 high value / next · 🟨 medium · 🟦 nice-to-have · 🔧 tech-debt/hardening

---

## 1. Procurement Comparison Portal — beyond the MVP

The MVP is a local, single-user Streamlit prototype that extracts vendor commercial bids and shows a light-normalized comparison. Natural extensions:

- 🟥 **Full scope normalization.** Adjust for delivery terms (FCA / EXW / CIF / site), spares & tools scope (in vs out of base), and optional items — the complete apples-to-apples restatement, not just currency/VAT/freight.
- 🟥 **Weighted vendor scoring & award recommendation.** Configurable weights across price, delivery time, payment terms, and technical compliance; output a ranked, justified recommendation. Compliance *gating* to flag/exclude non-compliant bids.
- 🟥 **Requirements-doc parsing → compliance baseline.** Extract the requirement spec (specs, thresholds such as H2S ≥ 50 ppm, site conditions) and check each bid against it, producing compliance flags and a should-cost anchor. (MVP stores the doc but doesn't parse it.)
- 🟨 **Full line-item / BOM extraction & reconciliation.** Extract every BOM line per vendor into structured rows and reconcile to the vendor's own stated totals (the "numbers must add up" gate).
- 🟨 **Revision & lineage tracking.** Track document revisions and supersession (`935` → `935(Rev1)`, spec "Superseded with MOM…", `CS [Rev.2]`) and preserve the clarification trail — Minutes of Meeting (MOM) and deviation forms — as auditable history, not just final numbers.
- 🟨 **Comparative-Statement export matching the client template.** Regenerate the client's exact `Comparative Statement (CS)` Excel layout (and a readable PDF/Markdown summary), not just a generic table.
- 🟨 **Live FX rates.** Pull exchange rates from an API (with an as-of timestamp) instead of manual entry; cache per project for auditability.
- 🟦 **Provenance surfaced in the UI.** Click any figure to see its source document, page/cell, and the adjustments applied — the "every number is defensible" promise made visible.
- 🟦 **Bottom-up should-cost.** Parametric estimates (cost per kW, cost per unit by engine origin) and a bottom-up estimate from the richest vendor's BOM to sanity-check lump sums.
- 🔧 **Robust document handling.** OCR for scanned PDFs, vision fallback for image-only pages, more quotation layouts, and better quote-doc detection within a vendor folder.

## 2. Productionizing the portal

- 🟥 **Authentication & multi-user / multi-tenant.** Accounts, roles (buyer / reviewer / admin), per-user project isolation.
- 🟥 **Hosting & deployment.** Move off local single-user: containerize the app, deploy on AWS (see §4), persistent storage, HTTPS, backups.
- 🟨 **Durable persistence.** Replace the filesystem project store with a database (project metadata, bids, adjustments, audit log); object storage for uploaded documents.
- 🟨 **Background processing.** Run extraction as async jobs (progress, retries, cancel) rather than blocking the request — aligns with the AWS event-driven design in §4.
- 🟦 **Collaboration.** Comments/annotations on bids, shareable read-only comparison links, export history.

## 3. Cost-estimation pipeline — beyond Phase 1 ingestion

Phase-1 ingestion of the GDES cost workbooks is built. Deferred phases (per the cost-estimation design §9):

- 🟥 **Rate library.** Build a reusable unit-rate library (labour + equipment + material + consumables + installation) from the populated COSTING workbooks, keyed by cost code / description.
- 🟥 **Pricing engine.** Apply the rate library to the blank Schedule of Prices → priced BOQ / should-cost, with the item→rate matching (deterministic code/description first, LLM for leftovers).
- 🟨 **Roll-up & proposal generation.** Discipline/project roll-up beyond echoing Summary values; generate the priced proposal from the `.docx` template.
- 🟨 **Resource rates & crew population.** Populate `resource_rates` / crew composition (schema exists; not filled — spec §6 currently overstates this).
- 🟦 **SOW method-of-measurement enforcement.** Apply the Scope-of-Work measurement rules referenced by the BOQ.
- 🟦 **Drawings / BOM deep-parse** for bottom-up estimation.

## 4. AWS infrastructure (design exists, unbuilt)

Per `docs/superpowers/specs/2026-07-27-aws-ingestion-infrastructure-design.md`:

- 🟥 **IaC build** — choose CDK-Python or Terraform and implement: S3 (input/output/audit) · EventBridge · Step Functions · ECS Fargate + ECR · DynamoDB · KMS · CloudTrail · SNS · SQS DLQ · VPC endpoints.
- 🟥 **Container packaging + batch entry point.** Package the pipeline into a container and add an S3-in / S3-out batch CLI entry point.
- 🟨 **dev + prod stacks** as isolated environments (Bedrock-only prod, no NAT; cheaper dev).
- 🟨 **Bedrock in prod path.** The `BedrockClient` adapter is built; wire `LLM_PROVIDER=bedrock` into the deployed config and confirm model/region availability.

## 5. Shared foundation / LLM layer

- 🟨 **Live-test OpenAI & Gemini adapters.** Currently implemented and mock-tested only; promote to live-tested paths.
- 🟨 **Vision fallback.** All adapters accept an `images` argument but ignore it; wire image content blocks so scanned/image-only pages can be extracted.
- 🟨 **Generalize the PDF LLM transcription fallback.** The portal's `procurement/pdf_llm.transcribe_pdf` (robust extraction for scanned/thin PDFs) is currently a direct Anthropic call via native PDF document input. Route it through the provider-agnostic LLM layer (a document/vision method on `LLMClient`) so it also works on Bedrock and other providers.
- 🟨 **Prompt caching & cost controls.** Enable automatic prompt caching for the repeated extraction prompt (Anthropic direct); add token-usage/cost logging per run.
- 🟦 **Streaming & resilience.** Streaming responses for long extractions; richer retry/backoff and rate-limit handling.

## 6. Tech-debt & hardening (from the SDD ledger and reviews)

- 🔧 `parse_summary` call in `ingest_workbook` is not inside the per-sheet `try/except` — a malformed Summary sheet can still raise.
- 🔧 Partial-sheet-failure is not surfaced when some sheets parse and others fail (status only "failed" when *all* fail).
- 🔧 Factory has no test for `openai`/`gemini` selection or `LLM_MODEL` → adapter-model passthrough (Bedrock now covered).
- 🔧 Untested branches: `doc_type` ordering, `classify_sheet` section-code fallback, substring discipline matching (no word boundary), `ingest_directory` branch logic; redundant `classify_document` call.
- 🔧 Real-data **item-arithmetic** reconciliation is vacuous on the blank Schedule of Prices (0 priced items); needs a populated fixture or live-LLM path.
- 🔧 Cost-estimation spec §6 wording overstates shipped scope (lists resource rates as delivered).
- 🔧 `dict(block.input)` redundant copy in the Anthropic/Bedrock adapters (harmless, consistent).

## 7. Cross-cutting engineering

- 🟨 **Packaging & environments.** Lock dependencies (`uv`/`poetry`), pin versions, reproducible setup; separate `procurement`, `cost_estimation`, `shared`, `portal` as clean packages.
- 🟨 **CI.** Run the test suite on push (mock-adapter path, no keys); lint/format.
- 🟨 **Observability.** Structured logging, run/audit records, and a way to inspect a past ingestion.
- 🟦 **Security review.** Secrets handling, upload validation (file type/size, zip-slip protection on vendor-ZIP extraction), dependency scanning.
- 🟦 **Golden-dataset tests.** A benchmark that reproduces the hand-built Comparative Statement from the real bids and explains every divergence — the regression net for extraction + normalization.

---

*Add to this document as new deferrals arise; keep items grouped by area and tagged by priority.*
