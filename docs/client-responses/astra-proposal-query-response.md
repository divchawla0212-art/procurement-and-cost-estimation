# Response to ASTRA Proposal Queries

**From:** BKS Solutions
**To:** ASTRA — Procurement / IT / Commercial review
**Subject:** Response to eight queries raised on the Procurement Comparison Portal proposal
**Date:** 18 August 2026
**Status:** For discussion. Commercial figures marked *indicative* are subject to the commercial schedule.

---

## Purpose and posture

Eight queries were raised. Three of them identify things our proposal did not state
clearly enough, and we accept those without argument. Four require information we
have but did not put in front of you. One — the proposal to run a measured POC on
real ASTRA data before committing — is the right next step and we agree with it
outright.

We have written this document to be checkable rather than reassuring. Where a
capability is built, we say what it is and how you can verify it during the POC.
Where a capability is **not** built, we say so in the same sentence rather than in a
footnote. Section 9 lists every such gap in one place, because a proposal that only
lists strengths is not one you can plan against.

---

## Summary of responses

| # | Query | Our position | Where |
|---|---|---|---|
| 1 | Extraction/comparison portal, not full procurement | **Partly accepted.** The deck under-represented the platform — the eight-stage RFQ lifecycle is built. But it is not a source-to-pay suite and we will not claim it is. | §1 |
| 2 | SaaS vs on-premise confusion | **Accepted.** The proposal mixed two delivery models. | §2 |
| 3 | AI architecture, residency, cost, model dependency, security undefined | **Accepted.** | §3, Appendix B |
| 4 | Customisation cost needs a deliverables matrix | **Accepted.** | §4, Appendix A |
| 5 | 10 named users impractical across four functions | **Accepted.** | §5 |
| 6 | Engineering AI scope too broad | **Accepted — withdrawn from Phase 1.** | §6 |
| 7 | Ownership, source code, data, prompts, exit | **Answerable in full.** | §7 |
| 8 | Run a measured POC first | **Agreed.** | §8, Appendix C |

---

## 1. "Mainly an AI document extraction and comparison portal rather than a complete procurement solution"

### What is fair in this

The pitch deck we sent describes seven steps, and all seven of them are the
comparison pipeline: intake, recognition, lineage, reading, filing, measurement,
decision. Read on its own, the deck supports exactly the conclusion you reached. That
is a failure of our document, not a misreading on your side.

### What the deck omitted

The platform is two subsystems, not one. Alongside the comparison pipeline there is a
complete RFQ lifecycle, built and under test, exposed through 47 HTTP endpoints:

| Capability | State | Notes |
|---|---|---|
| Eight-stage RFQ lifecycle | Built | Shortlisting → Issued → Clarifications → Bids Received → Evaluation → Negotiation → Awarded → PO Issued. Fixed process codes RFQ-02 … RFQ-07, deny-by-default stage transitions, and documented backward recoveries (retender, renegotiate). |
| Project → item → RFQ hierarchy | Built | An RFQ covers named line items within a project; deletion is refused while anything live references the target. |
| Bidder registry | Built | Organisation-wide, SQLite-backed, ~1,300 vendors folded from a real ADNOC Approved Vendor List export, plus an Astra-approved subset. Queried by discipline, product group and approval. |
| Vendor eligibility / prequalification | Built | Suitability computed from prequal status, expiry and scope fit. Expiry is derived at read time against a date the caller passes in — there is no overnight sweep that can leave the register stale. |
| Shortlisting and client approval | Built | Per-vendor invitation as an attributed act; inviting a blocked bidder requires a recorded override reason. Client approval is derived live from the AVL, so correcting one registry row corrects every shortlist that references it. |
| Draft shortlists per item | Built | A buyer assembles candidates before any RFQ exists; adoption into invitations happens when the RFQ is raised, with every skipped vendor reported by name and reason. |
| Clarification rounds | Built | Numbered queries, computed Open/Answered/Withdrawn state, restricted answers with a mandatory recorded reason, and grounded answer drafting from the enquiry package. |
| Addenda | Built | The single sanctioned route through the frozen-package rule; supersedes at a new revision, with concurrency guards. |
| Enquiry and bid document management | Built | Content-addressed storage. One collection holds both the enquiry the contractor issued and the documents bidders returned. |
| Technical bid evaluation (TBE) / VDRL | Built (server) | Routes served and tested; some browser controls were deliberately removed — see §9. |
| Discipline vocabulary | Built | Controlled vocabulary joined to the client's own product-group taxonomy, so scope matching resolves against the real register rather than free text. |
| Comparison pipeline | Built | The seven steps in the deck. |
| Cost estimation | Built (CLI only) | A separate costing-sheet ingestion CLI. Not reachable from the web application today. |

### What it is genuinely not

Stated directly, so that nobody plans against something that does not exist:

- **It is not a source-to-pay suite.** There is no PO issuance to an ERP, no goods
  receipt, no invoice matching, no payment, no contract lifecycle management, no
  supplier performance scorecarding, no e-auction, and no catalogue or punch-out.
- **Nothing sends mail.** Recipient addresses are recorded; there is no mail
  transport. An RFQ, an answer or an addendum is produced by the platform and issued
  by a person today. This is a named customisation item (Appendix A, C-3).
- **No ERP integration is built.** SAP/Oracle/other integration is a defined
  customisation deliverable, not a shipped feature.

### Proposed correction

We will re-issue the proposal with the scope stated as **"RFQ management and bid
evaluation"** rather than as a comparison portal, with the boundary above printed in
the document rather than discovered in review. If ASTRA's requirement is genuinely
source-to-pay, we would rather establish that now than in month four.

---

## 2. SaaS subscription vs on-premise deployment — where AI processing and data reside

### Accepted without qualification

The proposal used SaaS subscription language ("seats", "credits", "plans") alongside
security language about deploying "inside your perimeter". Those are two different
delivery models and we presented them as if they were one. The confusion is ours.

### The three models, stated separately

| | **A — BKS-managed single tenant** | **B — ASTRA-tenanted cloud** | **C — True on-premise** |
|---|---|---|---|
| Infrastructure owned by | BKS | **ASTRA** | ASTRA |
| Runs in | BKS AWS account, ASTRA-approved region | **ASTRA's own AWS account and region** | ASTRA data centre |
| Application data at rest | BKS-controlled encrypted storage | **ASTRA-controlled; ASTRA holds the KMS keys** | ASTRA storage |
| Model inference | Amazon Bedrock in-region, or Anthropic/OpenAI API | **Amazon Bedrock in ASTRA's own account and region** | Self-hosted open-weight model |
| Document text leaves ASTRA's cloud boundary? | Yes, to BKS's account | **No** | No |
| Leaves the country/region? | No (region-pinned) | **No** | No |
| Commercial shape | Subscription | Subscription + ASTRA pays its own cloud bill | Licence + support |
| Extraction quality | As measured | **As measured — same models** | **Unmeasured. Lower.** |
| Our recommendation | For the POC | **For production** | Only if policy forbids B |

### Our recommendation, and the reason

**Model B for production; Model A for the POC.**

Model B answers your question in the strongest available form: with Amazon Bedrock
running in ASTRA's own AWS account in an ASTRA-approved region, **document content
never leaves ASTRA's cloud boundary and never leaves the region.** There is no
outbound call to Anthropic's or OpenAI's public API, no third-party network hop, and
no cross-border transfer. Authentication is AWS IAM against ASTRA's own account — BKS
holds no API key that reaches your documents. Bedrock invocations are not retained by
AWS and are not used to train the underlying model.

We recommend Model A for the POC only because it is faster to stand up — days, rather
than the weeks an account, VPC, IAM and network-approval cycle takes inside ASTRA. If
ASTRA's security policy requires the POC itself to run in ASTRA infrastructure, we
will run it on Model B and adjust the schedule accordingly.

**On Model C, honestly.** The application itself runs on-premise without difficulty —
it is a single container with file-backed storage, no database server, no message
broker, no cache. What does not carry over is the model. A genuinely air-gapped
deployment means a self-hosted open-weight model, and **we have not measured
extraction accuracy on one.** We are not willing to quote an accuracy figure we have
not measured. If Model C is a hard requirement, we should add an open-weight
benchmark to the POC and price the outcome afterwards.

### What we need from ASTRA to close this

1. Confirmation of the required model (A, B or C).
2. The approved region (we assume UAE / `me-central-1` unless told otherwise).
3. Whether ASTRA requires customer-managed KMS keys.
4. Whether ASTRA IT provisions the account and network, or BKS deploys into an
   account ASTRA provides.

---

## 3. AI architecture, data flow, data residency, API costs, model dependency and security controls

### 3.1 Architecture

Model access sits behind a **single interface with one method**. Every extraction in
the system — quotations, technical datasheets, requirements, deviations, meeting
minutes, vendor suggestion — calls the same entry point. Behind it are five
interchangeable adapters:

| Adapter | Authentication | Data path | Status |
|---|---|---|---|
| **Amazon Bedrock** | AWS IAM, no key | Stays inside the AWS region | Built; live-testing is a POC task |
| Anthropic | API key | Outbound HTTPS to Anthropic | Built and in production use |
| OpenAI | API key | Outbound HTTPS to OpenAI | Built; mock-tested, **not live-tested** |
| Google Gemini | API key | Outbound HTTPS to Google | Built; mock-tested, **not live-tested** |
| Mock | none | No network at all | Used by the entire automated test suite |

Two properties of this design bear directly on your query:

- **Prompts are versioned files on disk**, not string literals buried in code. What
  is asked of the model is a reviewable, diffable artefact. ASTRA can be given read
  access to the prompt directory as a contractual condition — see §7.
- **Arithmetic stays in Python.** Extractors capture numbers and units *verbatim*
  from the vendor's document; code decides compliance, normalisation, FX conversion
  and ranking. **No model is ever asked whether a vendor complies.** This is the most
  important control in the system: an award recommendation is produced by
  deterministic, auditable, testable code operating on figures traced to a page — not
  by a model's judgement.

### 3.2 Data flow

Diagrammed in Appendix B. Per document:

1. Uploaded over TLS; archive entries validated against path traversal before
   anything is written to disk.
2. Text extracted **locally** (pypdf / python-docx / openpyxl / poppler). Binaries,
   images and drawings are not transmitted in the default configuration.
3. Extracted text split on line boundaries into budgeted chunks (12,000 characters
   technical, 8,000 requirements).
4. Each chunk sent to the configured model with the versioned prompt; returns
   structured JSON — figures and units, not conclusions.
5. Results merged all-or-nothing and stored locally against the source document, page
   and vendor.
6. Comparison, compliance checking, normalisation and ranking run entirely in Python
   over stored facts. **No model call occurs at this stage.**

Volume: approximately **13 MB of text leaves the boundary per 500 MB of source
documents** — and under Model B, that boundary is ASTRA's own AWS region.

### 3.3 Data residency

| Data class | Where it lives (Model B) | Crosses a border? |
|---|---|---|
| Uploaded documents | ASTRA S3, ASTRA region | No |
| Extracted facts, comparison results | ASTRA file store, ASTRA region | No |
| Accounts, sessions, audit trail | ASTRA file store, ASTRA region | No |
| Bidder registry | ASTRA file store, ASTRA region | No |
| Text sent for inference | Amazon Bedrock, same ASTRA region | **No** |
| Model outputs | Returned to ASTRA region, stored there | No |

**Training.** Under Bedrock's terms, model invocations are not retained and are not
used to train models. Under Anthropic's and OpenAI's commercial API terms, prompts
and outputs are excluded from training by default. We will attach the current
published terms to the contract and re-confirm them at signature, rather than asking
you to rely on this paragraph.

### 3.4 API costs — measured, not estimated

These figures come from replaying a real multi-vendor tender through the production
code, not from a model of what it might cost:

| Measure | Value | Basis |
|---|---|---|
| Model calls per tender | ~102 | measured — 37 documents, 4 vendors |
| Wall-clock per tender, today | ~34 minutes | measured, fully sequential |
| Source text yield | 18,470 characters per MB | measured, born-digital PDFs |
| Text amplification (prompt overhead, second passes) | ×1.46 | measured |
| Characters per token, this text shape | 3.41 in / 2.06 out | measured with the provider's own tokeniser |

**The assumption most likely to be wrong for ASTRA is the third one.** Born-digital
PDFs yield 18,470 characters per MB. Scanned drawings yield almost none and cost
almost nothing; spreadsheets can yield several times more. **Measuring this against
ASTRA's actual document mix is a primary objective of the POC** — it is the number
that drives your bill, and we will not contract a per-MB rate before we have measured
yours.

**One disclosure.** Token usage is **not currently metered inside the platform.** The
figures above were produced by an offline measurement harness. A usage meter with a
per-run cost record is a Phase 1 deliverable (Appendix A, C-6) and must be in place
before any consumption-based billing begins. We are telling you this rather than
letting you discover that the meter behind the pricing model does not yet exist.

**Cost containment.** Two levers are available and both are in Phase 1 scope:
adopting the provider's Batch API roughly halves inference cost with no change to
output, and prompt caching removes repeated cost on the fixed extraction
instructions.

### 3.5 Model dependency

A fair concern, and the design already answers most of it.

- **Switching provider is a configuration change**, not a code change — one
  environment variable, overridable per ingestion run. The abstraction exists
  precisely so that no single provider is load-bearing.
- **Under Model B, ASTRA's dependency is on AWS Bedrock**, not on Anthropic or OpenAI
  as counterparties. Bedrock offers multiple model families, so a model deprecation
  is a model-id change within an existing contract.
- **Model versions are pinned.** The model id is explicit configuration. ASTRA is
  never silently migrated to a new model. Any model change is a change-controlled
  event with a re-run of the accuracy benchmark against the POC corpus.
- **Honest caveat.** The OpenAI and Gemini adapters are built and unit-tested but have
  not been exercised against live endpoints. If ASTRA wants a contractually
  guaranteed second provider, live-testing that adapter is a named POC deliverable —
  not something to assume.

### 3.6 Security controls

Built and testable today:

| Control | Implementation |
|---|---|
| Authentication | Fail-closed. Every API path requires a session except exactly three (health, login, signup), and that set is pinned by an automated test, so a new endpoint is protected the day it is written. |
| Passwords | `scrypt` with a per-password salt. The digest is structurally incapable of appearing in an API response. |
| Sessions | Only `sha256(token)` is stored. Browser-session cookies with no persistence. Restarting the service signs everyone out. |
| Authorisation | Roles (`admin`, `reviewer`) set once at creation; no endpoint can change a role. Per-project grants enforced server-side on every route, not by hiding navigation. |
| Upload safety | One traversal guard for the whole system, deliberately strict: a `..` segment is refused on the segment rather than on where it resolves, and both path separators are normalised first — so an attack valid only on Windows is still refused on Linux. Requirements uploads restricted by extension. |
| Transport | TLS everywhere, including every model call. |
| At rest | Managed encryption; customer-managed KMS keys available under Model B. |
| Audit trail | Every classification, extraction, override, stage transition and re-run recorded with actor and timestamp. RFQ history is append-only — a backward stage transition appends an entry, it never rewrites one. |
| Secrets | Never baked into an image unless a build flag is passed explicitly; environment files are excluded from the build context. |
| Concurrency correctness | Every write, *and every decision that gates a write*, executes inside a single critical section. |

**Two known exposures, disclosed rather than hidden.** Both are development
conveniences that must be closed before any ASTRA deployment, and both are named
Phase 1 hardening items (Appendix A, C-7):

1. A development authentication bypass exists (off by default) that serves an
   unauthenticated caller as the first administrator. It must be verified off and the
   flag removed from production builds.
2. The current sign-in screen carries a demo-account convenience control that embeds
   a demo password in the browser bundle. It is removed for ASTRA.

**Not yet in place**, and we would rather say so: SSO / SAML / Entra ID (customisation
item C-4), MFA, IP allow-listing, a penetration test report, and SOC 2 attestation
for BKS as an organisation. The infrastructure layer's certifications (AWS: SOC 1/2/3,
ISO 27001/27017/27018, PCI DSS Level 1) are **AWS's, not ours**, and we will not
present them as ours.

---

## 4. Customisation cost — deliverables matrix

### Accepted

A customisation figure with no deliverables schedule behind it is an invitation to a
change-order dispute, and you are right to refuse it. **Appendix A is a fixed-scope
matrix**: every deliverable named, quantified with a unit of measure, and either
inside the fixed price or explicitly outside it.

### The four principles we propose contracting on

1. **Every deliverable carries a countable unit** — "3 RFQ templates", "2 approval
   workflows", "1 ERP integration, 4 endpoints" — never "workflow configuration".
2. **Anything not named in Appendix A is outside the fixed price**, and both parties
   can see that at signature rather than at month four.
3. **A change request is priced against a published day rate before work begins**,
   never invoiced afterwards.
4. **A configuration change is not a change request.** Adding a discipline, a vendor,
   an RFQ template, a user or a report filter is self-service and free forever. The
   line between configuration and development is drawn in Appendix A explicitly,
   because that line is where change-order disputes actually start.

### Use-case coverage

Appendix A quantifies Phase 1 as **6 procurement use cases** covered end to end. We
propose that the POC (§8) confirms this list against ASTRA's real process before the
customisation price is fixed — pricing a workflow build before seeing your actual MR
and RFQ flow is how an estimate goes wrong.

---

## 5. The 10-named-user limit

### Accepted

Ten seats was sized for a pilot — one procurement team proving the tool on real
tenders. It is the wrong shape for a platform used across Procurement, Engineering,
Estimation and Projects, and holding you to it would produce exactly the outcome
neither of us wants: people sharing logins, which destroys the attributed audit trail
that is one of the platform's main arguments.

### Three structures, for ASTRA to choose

| | **Option 1 — Named seats** | **Option 2 — Functional bands** | **Option 3 — Enterprise** |
|---|---|---|---|
| Shape | Per named user | Per department band | Unlimited named users |
| Best when | Under ~30 users | 30–100 users across four functions | Org-wide standard |
| Included | 10 seats, then $75/user/month *(indicative)* | Banded at 25 / 50 / 100 seats | All ASTRA staff |
| Usage allowance | Shared pool — extra seats draw on the same allowance and are never rationed | Shared pool | Shared pool |
| Our view | Fine for the POC | **Recommended for rollout across four functions** | If ASTRA standardises platform-wide |

**Two commitments regardless of which is chosen:**

- **An additional seat is never charged twice.** It carries no separate usage
  allowance; it draws on the account's shared pool, so a user's work is billed once
  through the meter or not at all.
- **Read-only and occasional users.** Approvers, engineering reviewers and management
  who read a comparison but never run one should not consume a full seat. We propose
  a **read-only role at no charge**, capped at a multiple of paid seats, written into
  the commercial schedule.

**Final seat counts should be set from what the POC measures**, not guessed now. We
would rather size this from your real usage than sell you fifty seats and have
thirty-one of them idle.

---

## 6. Engineering AI scope

### Accepted without reservation

The Engineering AI scope in the proposal is broad, unmeasured and not costed against
deliverables. We are **withdrawing it from Phase 1** rather than defending it.

### Why

Everything we have quoted an accuracy or cost figure for in this document was
measured against a real tender corpus. We have no equivalent measurement for the
engineering scope, and a scope with no measurable acceptance criterion cannot be
accepted, cannot be tested, and cannot be signed off. Including it would place a
large unmeasured item beside a set of measured ones and lend it credibility it has
not earned.

### Proposed re-scoping

**Phase 1 (contracted now):** RFQ management and bid evaluation. No engineering AI.

**Phase 3 (optional, priced separately once Phase 1 is live):** two or three named
engineering use cases, each with a written acceptance criterion, each independently
cancellable. Candidates, for ASTRA to select and rank:

| Ref | Deliverable | Measurable acceptance criterion |
|---|---|---|
| E-1 | Technical deviation extraction — structured deviation register from vendor technical submittals | ≥ X% recall against a hand-built register on N real submittals |
| E-2 | Datasheet parameter extraction — named parameters per equipment class | ≥ X% field accuracy on N datasheets, per class |
| E-3 | Specification compliance check — requirement-by-requirement compliance matrix | ≥ X% agreement with an engineer's manual matrix |
| E-4 | Drawing / BOM extraction | **Not proposed.** Requires vision extraction we have not measured. |

X and N to be set jointly from the POC baseline. **We will not propose E-4 until we
have measured it** — the same discipline that led us to withdraw the broad scope in
the first place.

---

## 7. Ownership of the solution, source code, data, AI prompts and models — and continuity if BKS support ends

Answerable in full, and we regard the answers below as contractual.

### 7.1 Ownership

| Asset | Owner | Notes |
|---|---|---|
| **All ASTRA data** — documents, extracted facts, comparisons, bidder registry, audit trail, user accounts | **ASTRA, unconditionally** | Yours during the term and after it. Not conditioned on payment status, notice period or renewal. |
| **ASTRA-specific configuration** — RFQ templates, disciplines, approval workflows, report layouts, integration mappings | **ASTRA** | Delivered as source files, not as opaque database rows. |
| **ASTRA-specific prompts** — any prompt tuned to ASTRA's document formats | **ASTRA** | Versioned files, delivered with the configuration. |
| **Base platform source code** | **BKS**, licensed perpetually to ASTRA for internal use | See 7.3 for the continuity mechanism. |
| **Base platform prompts** | BKS, provided to ASTRA under the licence | ASTRA gets read access for audit — a prompt you cannot read is a control you cannot verify. |
| **Underlying AI models** | Anthropic / OpenAI / AWS | Neither party owns these; both are third-party services. |

**ASTRA's data is never used to improve a product sold to anyone else.** No ASTRA
document, extraction or comparison is used to tune prompts, build benchmarks or train
anything used outside ASTRA's own deployment. We will contract on this.

### 7.2 Exit and migration — the strongest answer we can give you

The architecture makes this straightforward, because there is no proprietary database
and no proprietary format anywhere in it:

| What you take out | Format | Readable without BKS? |
|---|---|---|
| Every uploaded document | Original files, unmodified, in a plain directory tree | **Yes — they are your files** |
| Extracted facts and comparison results | JSON, one file per collection | **Yes — any text editor** |
| Audit / event log | JSONL, append-only | **Yes** |
| Bidder registry | SQLite (open format) + CSV/XLSX export | **Yes** |
| RFQ workflow — projects, items, RFQs, shortlists, clarifications | One JSON document | **Yes** |
| Accounts and grants | JSON | **Yes** |
| Comparisons and compliance matrices | CSV and XLSX export | **Yes — Excel** |
| Configuration and prompts | Plain text / YAML files | **Yes** |

There is **no proprietary binary format, no encrypted vendor-locked store, and no
data structure that requires BKS software to interpret.** A full export is a copy of a
directory. We propose contracting a **quarterly automated export to an ASTRA-owned S3
bucket**, so a current copy is continuously in ASTRA's hands rather than requested at
the point of dispute.

### 7.3 If BKS support is discontinued

Four mechanisms, in ascending order of protection. ASTRA should select the level
appropriate to how critical the platform becomes:

1. **Source code escrow** with a recognised agent (NCC or equivalent), released on
   defined triggers: insolvency, acquisition without novation, or failure to meet the
   support SLA for a defined period.
2. **Perpetual internal-use licence**, so on termination ASTRA may continue running
   the version in place indefinitely. Under Model B this is meaningful rather than
   theoretical — the software already runs in ASTRA's own account.
3. **Documented handover**: deployment runbook, architecture documentation and
   configuration inventory maintained as a contractual deliverable, rather than
   produced under exit pressure.
4. **Transition assistance**: a defined number of days' support at a pre-agreed rate
   during a 90-day transition, whichever party terminates.

**One thing worth stating plainly.** Under Model B, if BKS disappeared tomorrow, the
platform keeps running. It is a container in ASTRA's account, calling Bedrock in
ASTRA's account, reading and writing files ASTRA owns. Nothing calls home to BKS and
no licence check phones out. What ASTRA loses is future development and support — not
access to the system or to the data.

---

## 8. The proposed POC — agreed

We agree, and we would have proposed it had you not. This is what the pilot in our
proposal was for; your framing is better than ours, because it names the acceptance
criteria up front.

### Design

| | |
|---|---|
| **Duration** | 6 weeks — 4 weeks execution, 2 weeks measurement and report |
| **Input** | ASTRA's real MR/RFQ packages and vendor quotations — **not a demonstration set** |
| **Volume** | 3–5 completed tenders that ASTRA has *already evaluated manually*, so there is a known-correct answer to measure against |
| **Deployment** | Model A (BKS single tenant, ASTRA-approved region) unless ASTRA policy requires Model B |
| **Users** | 5–8 ASTRA users across Procurement and Engineering |
| **Fee** | $7,500, **credited in full** against a first-year agreement signed within 60 days of completion |
| **If ASTRA stops at week 6** | ASTRA keeps every comparison, every export and the full measurement report. Nothing to unpick. |

### The seven things you asked us to measure

| # | Measure | Method | Proposed pass mark |
|---|---|---|---|
| 1 | **Extraction accuracy** | Field by field against ASTRA's own manually built comparative statement | ≥ 95% commercial fields; ≥ 90% technical parameters *(to confirm)* |
| 2 | **Comparison accuracy** | Does the ranked outcome match the answer ASTRA's team reached by hand — and where it differs, which one is right? | 100% agreement on the compliant / non-compliant call, or a documented reason for every divergence |
| 3 | **Time saving** | ASTRA records hours spent manually; we record hours on the same tenders through the platform | ≥ 60% reduction *(to confirm)* |
| 4 | **Security** | ASTRA IT/security review against §3.6, plus a documented data-flow walkthrough | ASTRA sign-off, no unresolved high findings |
| 5 | **Integration effort** | Scoping workshop with ASTRA IT on ERP, SSO and mail | A costed integration plan, not an estimate |
| 6 | **Total cost of ownership** | Measured token consumption on ASTRA's own document mix, plus infrastructure, plus support | A 3-year TCO built from measured figures, not from our planning rates |
| 7 | **Document-mix calibration** | Measured characters-per-MB across ASTRA's actual scanned / born-digital / spreadsheet mix | Baseline established — **becomes the contractual basis for any usage pricing** |

Full criteria in Appendix C.

**Measure 7 is the one we most want from this POC**, because it is the number our
entire cost model rests on and we currently hold ASTRA's value at zero. Our planning
rate is 18,470 characters per MB, measured on born-digital PDFs. If ASTRA's packages
are heavily scanned, that figure collapses and so does the cost; if they are
spreadsheet-heavy, it rises. **We are not willing to contract a usage rate against
ASTRA's documents before we have measured ASTRA's documents.**

### What we need from ASTRA to start

1. Three to five completed, manually evaluated tender packages, with the comparative
   statement your team produced.
2. The hours those tenders actually took, if recorded — otherwise a considered
   estimate from the people who did the work.
3. A named ASTRA POC owner and 5–8 users.
4. A security-review slot with ASTRA IT in week 1, not week 6.
5. Agreement on the pass marks above **before** we start, so the result is a fact
   rather than an argument.

---

## 9. Consolidated gap register

Every limitation named anywhere in this document, in one place, so ASTRA can plan
against it. This section exists because we would rather lose on an honest scope than
win on an implied one.

| # | Gap | Impact | Plan |
|---|---|---|---|
| G-1 | No mail transport — nothing sends an RFQ, answer or addendum to a bidder | RFQ issue is a manual send today | Customisation item C-3 |
| G-2 | No ERP / SAP integration built | Manual PO handoff | Customisation item C-1, scoped in POC |
| G-3 | No SSO / SAML / Entra ID | Separate credentials | Customisation item C-4 |
| G-4 | Token usage not metered in-platform | Consumption billing cannot start | Phase 1 deliverable C-6, **before** any usage billing |
| G-5 | Ingestion runs synchronously — ~34 min per tender, one at a time | Throughput ceiling | Queue + parallel workers, Phase 1. Target ~4–8 min per tender at concurrency 8 |
| G-6 | OpenAI and Gemini adapters not live-tested | Second-provider guarantee unproven | Live-test as a POC deliverable if ASTRA wants a contracted alternate |
| G-7 | Bedrock adapter built but not yet wired into a deployed configuration | Model B depends on this | POC deliverable — the first thing we do |
| G-8 | Remote object storage (S3) backend not implemented | Local / EFS storage only today | Phase 1. The storage interface already exists, so this is an addition, not a rebuild |
| G-9 | Development auth bypass and demo-credential control present in the codebase | Must not reach an ASTRA deployment | Phase 1 hardening C-7, verified in the POC security review |
| G-10 | Vision extraction for scanned drawings not enabled | Image-only drawings yield no text today | Deliberate — off by default and costs nothing. Enabling it changes the cost model materially and is a priced decision after the POC measures how much of ASTRA's corpus is scanned |
| G-11 | Cost-estimation module is CLI-only, not in the web application | Not usable by ASTRA today | Out of Phase 1 scope; Phase 3 candidate |
| G-12 | No SOC 2 attestation for BKS as an organisation | ASTRA relies on AWS's certifications plus contractual terms | Disclosed; on the BKS roadmap, not claimed today |
| G-13 | No penetration test report | ASTRA may require one | Can be commissioned before production go-live if ASTRA requires it |
| G-14 | On-premise (Model C) extraction accuracy unmeasured | Model C cannot be quoted with an accuracy figure | Add an open-weight benchmark to the POC **only if** Model C is a hard requirement |

---

## 10. What we propose happens next

| # | Action | Owner | By |
|---|---|---|---|
| 1 | ASTRA confirms deployment model (A / B / C) and approved region | ASTRA | Week 1 |
| 2 | ASTRA confirms whether source-to-pay scope is required, or whether RFQ + evaluation is the target | ASTRA | Week 1 |
| 3 | ASTRA selects seat structure (Option 1 / 2 / 3) for pricing purposes | ASTRA | Week 2 |
| 4 | ASTRA ranks Phase 3 engineering candidates E-1 to E-3, or defers them | ASTRA | Week 2 |
| 5 | ASTRA selects continuity level (escrow / perpetual licence / both) | ASTRA | Week 2 |
| 6 | Joint workshop: agree POC pass marks and finalise Appendix A | Both | Week 2 |
| 7 | Re-issued proposal — corrected scope, single deployment model, fixed deliverables matrix | BKS | Week 3 |
| 8 | POC starts | Both | Week 4 |

---

# Appendix A — Customisation deliverables matrix

*To be finalised jointly in the Week 2 workshop. Quantities indicative pending the
POC. Once signed, this matrix **is** the definition of the fixed price.*

## A.1 Inside the fixed customisation price

| Ref | Deliverable | Unit of measure | Qty | Acceptance |
|---|---|---|---|---|
| **Workflows** | | | | |
| C-W1 | ASTRA RFQ approval workflow configured to ASTRA's delegation-of-authority matrix | approval workflows | 2 | A test transaction through each, signed off by the ASTRA process owner |
| C-W2 | ASTRA stage-gate criteria mapped onto the eight built-in stages | stage-gate rules | up to 8 | Each gate refuses and permits correctly on a test RFQ |
| C-W3 | Technical bid evaluation workflow with ASTRA's scoring model | evaluation workflows | 1 | Produces ASTRA's TBE output on a POC tender |
| **Templates** | | | | |
| C-T1 | RFQ document templates in ASTRA format | templates | 3 | Comparable to an ASTRA-issued RFQ |
| C-T2 | Comparative statement matching ASTRA's exact Excel layout | templates | 1 | Opens in ASTRA's own CS workbook without rework |
| C-T3 | Clarification / query register template | templates | 1 | ASTRA sign-off |
| C-T4 | Technical bid evaluation report template | templates | 1 | ASTRA sign-off |
| **Integrations** | | | | |
| C-1 | ERP integration — vendor master + PR/PO | endpoints | 4 | Round-trip test against ASTRA's non-production ERP |
| C-3 | Outbound mail transport — RFQ issue, clarification answers, addenda | integrations | 1 | RFQ issued to a test mailbox with a delivery record |
| C-4 | SSO — SAML 2.0 / Entra ID | integrations | 1 | ASTRA credential signs in; local passwords disabled |
| C-5 | Vendor master import from ASTRA's AVL | importers | 1 | Full ASTRA AVL loaded, row count reconciled |
| **Reports** | | | | |
| C-R1 | Standard reports — spend by discipline, cycle time by stage, vendor participation, clarification ageing | reports | 4 | Each renders on POC data and exports to XLSX |
| C-R2 | Management dashboard | dashboards | 1 | ASTRA sign-off |
| **Platform** | | | | |
| C-6 | Token/usage metering with per-run cost record and threshold alerts | 1 | 1 | Meter reconciles to the provider invoice within 5% |
| C-7 | Security hardening — dev bypass removed, demo control removed, production build verified | 1 | 1 | Verified in the ASTRA security review |
| C-8 | Queue + parallel workers (throughput) | 1 | 1 | A tender completes in ≤ 8 minutes at concurrency 8 |
| **Enablement** | | | | |
| C-E1 | Administrator training | sessions | 2 | Attendance + an admin task completed unaided |
| C-E2 | End-user training | sessions | 4 | Attendance + a tender run unaided |
| C-E3 | Documentation — admin guide, user guide, runbook | documents | 3 | ASTRA sign-off |

**Use cases covered end to end by the above: 6** — (1) raise an RFQ from an MR;
(2) shortlist from the AVL with approval; (3) issue an enquiry package; (4) run a
clarification round; (5) receive and compare bids; (6) produce the TBE and
comparative statement for award.

## A.2 Free configuration — never a change request

Adding or editing: disciplines and product groups · vendors and vendor contacts ·
users, roles and project grants · RFQ metadata fields · report filters and saved
views · approval thresholds within an existing workflow · currencies and FX rates ·
document classes and file-naming rules · email recipient lists.

## A.3 Explicitly outside the fixed price

Additional ERP endpoints beyond the 4 in C-1 · additional integrations beyond C-3 to
C-5 · a second ERP · custom mobile applications · contract lifecycle management ·
invoice matching or payment · e-auction · supplier performance scorecarding ·
catalogue / punch-out · engineering AI use cases (§6, Phase 3) · migration of
historical tenders predating go-live · custom reports beyond the 4 in C-R1.

Each priced against a published day rate, quoted and approved **before** work begins.

---

# Appendix B — Data flow

```
                  ASTRA APPROVED REGION  ·  ASTRA AWS ACCOUNT   (Model B)
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │                                                                             │
 │  ASTRA user ──TLS──► Load balancer ──► Web / API container                  │
 │  (browser)                                   │                              │
 │                                              │ 1. upload validated          │
 │                                              │    traversal guard,          │
 │                                              │    type, size                │
 │                                              ▼                              │
 │                                          Job queue                          │
 │                                              │                              │
 │                                              ▼                              │
 │                                       Worker container                      │
 │                                              │                              │
 │                     2. TEXT EXTRACTED LOCALLY                               │
 │                        pypdf · python-docx · openpyxl · poppler             │
 │                        binaries, images and drawings not transmitted        │
 │                                              │                              │
 │                     3. chunked on line boundaries                           │
 │                        12,000 chars technical · 8,000 requirements          │
 │                                              ▼                              │
 │                            ┌────────────────────────────────┐               │
 │                            │  Amazon Bedrock                │               │
 │                            │  SAME REGION · SAME ACCOUNT    │               │
 │                            │  IAM auth · not retained       │               │
 │                            │  not used for training         │               │
 │                            └────────────────────────────────┘               │
 │                                              │                              │
 │                     4. structured JSON returned                             │
 │                        figures + units VERBATIM — never a verdict           │
 │                                              ▼                              │
 │                     5. stored against source document, page, vendor         │
 │                                              │                              │
 │              ┌───────────────────────────────┼──────────────────┐           │
 │              ▼                               ▼                  ▼           │
 │       Document store                  Fact snapshots      Audit log         │
 │       S3, content-addressed           JSON                JSONL, append-only│
 │              │                               │                              │
 │              └───────────────────────────────┴──► 6. COMPARISON ·           │
 │                                                      COMPLIANCE · FX ·      │
 │                                                      NORMALISATION ·        │
 │                                                      RANKING                │
 │                                                      100% deterministic     │
 │                                                      Python.                │
 │                                                      NO MODEL CALL HERE.    │
 │                                                                             │
 └─────────────────────────────────────────────────────────────────────────────┘

   Nothing crosses this boundary.  No call to BKS.  No call to a public model API.
```

**Under Model A**, the boundary is a BKS-controlled account in an ASTRA-approved
region, and steps 3–4 may run against Amazon Bedrock (in-region) or against the
Anthropic API (outbound; no training, no retention beyond abuse screening) — ASTRA's
choice, recorded in the contract.

---

# Appendix C — POC acceptance criteria

*Pass marks proposed. To be agreed in the Week 2 workshop **before** the POC starts,
so that the result is a measurement rather than a negotiation.*

| # | Criterion | Method | Proposed pass | Evidence produced |
|---|---|---|---|---|
| 1a | Commercial field extraction accuracy | Field by field vs ASTRA's manual comparative statement, all POC tenders | ≥ 95% | Per-field diff report, every divergence listed |
| 1b | Technical parameter extraction accuracy | Vs ASTRA's manual technical comparison | ≥ 90% | Per-parameter diff report |
| 1c | Document classification accuracy | Vs manual classification of every file in the POC corpus | ≥ 95% | Confusion matrix |
| 1d | Revision lineage correctness | Every superseded document correctly retired | 100% | Lineage report |
| 2a | Ranked outcome agreement | Platform ranking vs ASTRA's manual award recommendation | Match, or a documented reason per divergence | Side-by-side ranking |
| 2b | Compliance determination | Compliant / non-compliant call per vendor per requirement | 100% agreement, or a documented reason | Compliance matrix vs ASTRA's |
| 2c | Traceability | Every figure traceable to source document and page | 100% | Spot-check of 50 random figures |
| 3 | Time saving | ASTRA-recorded manual hours vs platform hours, same tenders | ≥ 60% reduction | Time log, both methods |
| 4a | Security review | ASTRA IT review against §3.6 + data-flow walkthrough | Sign-off, no unresolved high findings | Review record |
| 4b | Data residency verification | ASTRA verifies no egress outside the approved region | Verified by ASTRA | Network / flow evidence |
| 5 | Integration effort | Workshop with ASTRA IT on ERP, SSO, mail | A costed plan, not an estimate | Integration plan with day counts |
| 6 | Total cost of ownership | Measured tokens on ASTRA's mix + infrastructure + support | 3-year TCO from measured figures | TCO model, every assumption sourced |
| 7 | Document-mix calibration | Characters-per-MB across ASTRA's actual scanned / born-digital / spreadsheet mix | Baseline established | Calibration report — **the contractual basis for usage pricing** |
| 8 | Throughput | Wall-clock per tender under production configuration | ≤ 8 min at concurrency 8 | Timing log |

**If criteria 1, 2 and 3 are not met, ASTRA does not proceed and owes nothing
further.** The $7,500 covers the work either way, and ASTRA keeps every output and the
full measurement report. We would rather find that out in six weeks on five tenders
than in year one on five hundred.

---

*Prepared by BKS Solutions in response to ASTRA's review of the Procurement Comparison
Portal proposal, 18 August 2026. Technical statements in §1, §3 and §7 describe the
platform as built and are verifiable during the POC. Commercial figures marked
indicative are subject to the commercial schedule.*
