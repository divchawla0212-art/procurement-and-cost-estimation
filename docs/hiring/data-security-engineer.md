# Software Engineer — Platform & Data Security

**Team:** Procurement Platform
**Reports to:** Engineering Lead
**Location / pattern:** _[to be completed — office, hybrid or remote]_
**Employment type:** _[to be completed — full-time / contract]_
**Level:** Mid to Senior (approx. 4+ years)

---

## Why this role exists

We build the platform that runs competitive tendering end to end: an enquiry
package goes out to a shortlist of vendors, sealed bids come back, and the
platform extracts, normalises and compares them so a buyer can award on evidence
rather than on a spreadsheet somebody maintained by hand.

That makes the product a custodian of other people's commercially sensitive
information. On any given tender we hold:

- **Vendor bids that compete with each other.** Bidder A's pricing, lead times
  and technical deviations must never become visible to Bidder B. This is not a
  compliance checkbox; it is the property that makes the tender legitimate at
  all. A leak invalidates the award.
- **Client reference data.** Approved-vendor registers and prequalification
  records supplied by clients, covering roughly 1,300 real named companies.
  These are client documents, not ours to redistribute.
- **Commercial terms and award decisions** with audit consequences.

We already treat this seriously — access is fail-closed, roles and per-project
grants are enforced server-side on every route, and the codebase carries written
invariants that reviewers hold each other to. What we do not yet have is an
engineer who owns that surface as their primary responsibility. That is this
role.

---

## What you will own

**1. The access-control surface.**
Authentication, sessions, roles and per-project grants across roughly 73 HTTP
routes. Our rule is that a new route is protected the day it is added, not the
day someone notices — you own the mechanisms and the tests that keep that true
as the route count grows.

**2. Tenant and bidder isolation.**
Designing and proving the boundaries that keep one bidder's submission out of
another's view, and one client's project data out of another client's. This
includes the harder half: proving it, with tests that fail when the boundary is
broken rather than tests that pass because nothing exercised them.

**3. Untrusted input handling.**
The platform ingests archives, PDFs, spreadsheets and whole folder trees
uploaded from a browser. You own path traversal, extraction limits, type
restriction, and content-addressed blob storage — including the invariant that a
stored file is reachable only through a live record that references it.

**4. Data flow to third-party model providers.**
Document extraction runs through external LLM providers (Anthropic, OpenAI,
Gemini, AWS Bedrock). That means client and vendor documents cross an
organisational boundary. You will own the answer to *what leaves, to whom, under
what contract, with what retention, and how a client is told* — including
building the controls that let a client's data be restricted to a specific
provider, a specific region, or to no external provider at all.

**5. Secrets, credentials and deployment posture.**
Provider API keys, IAM roles, container build hygiene, and keeping secrets out
of images and browser bundles.

**6. The remediation backlog.**
We keep a written record of known, deliberate security trade-offs — development
bypasses and demo-mode conveniences that are safe on a workstation and are not
safe on a reachable port. They are documented rather than hidden, precisely so
they can be closed on a schedule. You will own that schedule and close them.

**7. Data residency and privacy readiness.**
Our client base is regional and increasingly asks where data physically sits.
You will lead the work to answer that credibly — residency options, retention
and deletion, data-processing documentation, and readiness for client security
questionnaires and audits.

---

## What we are looking for

### Essential

- **Strong application-security instincts in a real codebase.** You can read a
  route handler and see the missing authorisation check, the check made outside
  the lock, the identifier taken from the request instead of the session.
- **Python, professionally.** We run FastAPI on Python 3.12 with Pydantic models
  throughout.
- **You can work across the stack.** The front end is React 19 with TypeScript.
  You do not need to be a front-end specialist, but a security engineer who
  cannot follow the data into the browser will miss half the findings.
- **Practical knowledge of the standard failure modes** — the OWASP Top 10 as
  lived experience rather than recited: broken access control, injection,
  insecure deserialisation, SSRF, secrets management, session handling.
- **Authentication and authorisation done properly.** Password hashing choices
  and why, session lifecycle, token storage, role and grant models, and the
  difference between hiding a control and denying the request.
- **Concurrency-aware thinking.** Several of the sharpest defects in this
  codebase have been a *read that gated a write* sitting outside the lock that
  protected the write. Two operators each read "this is fine" and both writes
  land. If that class of bug is familiar to you, say so in your application.
- **Testing as the deliverable.** A security fix here is not done until there is
  a test that fails without it. We are explicit about this: an assertion nobody
  has watched fail is not known to be wired to anything.
- **Clear written reasoning.** You will write the documents that our clients'
  own security teams read.

### Desirable

- Data-protection regulation relevant to the Gulf and to international clients —
  UAE PDPL, GDPR, or comparable.
- Cloud security on AWS or Azure: IAM, KMS and envelope encryption, network
  boundaries, private endpoints, secrets managers.
- Container and supply-chain hardening — image scanning, SBOMs, dependency
  pinning, build provenance.
- Experience with the security and privacy questions specific to LLM systems:
  provider data-retention terms, prompt and output handling, and treating
  model-supplied text as untrusted data rather than as instructions.
- Threat modelling you have actually run with a team, not only read about.
- Exposure to procurement, tendering or contracting workflows, and to why bid
  confidentiality is treated as sacrosanct.
- Experience preparing for or surviving a client security audit, penetration
  test, or ISO 27001 / SOC 2 process.

### Not required

- Certifications. Welcome, never a filter.
- Offensive security specialisation. This is a build-and-harden role. You will
  find things, but the job is fixing them and keeping them fixed.
- Prior LLM experience. Judgement about data crossing a boundary transfers; the
  model-specific parts we can teach.

---

## Your first 90 days

**Weeks 1–4 — read and map.** Get the platform running locally, read the
invariant documentation, and produce a data-flow map: every place client or
vendor data is stored, every place it leaves the process, and every place a
permission decision is made. We expect the map to find things we have not
written down.

**Weeks 5–8 — a threat model and a ranked backlog.** Turn the map into a threat
model for the tender lifecycle, with bidder-to-bidder isolation as the primary
asset. Merge it with the existing recorded trade-offs and give us one ranked
list with your reasoning, including what you propose *not* to do and why.

**Weeks 9–12 — close the top of the list, with tests.** Ship the highest-ranked
items and the tests that hold them. By day 90 we should be able to hand a client
a written, accurate answer to "where does our data go and who can see it".

---

## How we work

- Small team, high review standards, and a codebase where the *why* is written
  down. Design decisions and their trade-offs are recorded — including the ones
  we chose against — so that nobody later "fixes" a deliberate absence.
- Test-first for anything that guards an invariant.
- We state known exposures out loud rather than quietly carrying them. If you
  find something, the expectation is that you say so plainly, including when it
  is ours.
- Continuous integration runs on every pull request with no provider secrets
  available; anything requiring a credential sits behind a skip guard, never
  behind a repository secret.

---

## To apply

Send a CV and a short note. In place of a cover letter we would rather have a
few paragraphs on **one access-control or data-isolation bug you found or fixed**
— what the flaw was, how you found it, what you shipped, and what test now stops
it coming back. A near-miss you caught in review counts.

_[Compensation range, benefits and application contact to be completed before
posting.]_
