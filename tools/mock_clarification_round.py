"""Play a mock RFQ clarification round against a running API.

Both sides of the round: five queries raised by three shortlisted vendors, and
Astra's answers, one restriction, one withdrawal and one addendum. The script
that drives it is `tools/mock_clarification_round.json`; this module only plays
it and checks what came back.

Run it from the repo root with the app up (`.\\run.ps1`, or `-NoAuth`):

    python -m tools.mock_clarification_round
    python -m tools.mock_clarification_round --email admin@gmail.com --password Admin@1234
    python -m tools.mock_clarification_round --rfq rfq_ruu02
    python -m tools.mock_clarification_round --project prj_x --item itm_y --until Q6

**This is an infrastructure check, not a data load.** It goes over HTTP, so it
exercises the session middleware, the request models, `locked_update`, the
persisted document and the exit gate — none of which a store-level seed touches.
The `expect_gate` steps in the fixture are the assertions: the round is arranged
so the Clarifications gate is read three times, with both halves of its sentence
true, then one, then none. A run that loads every record and reports a green
gate at step one has found a real bug, and this script exits non-zero for it.

Two modes:

* **Bootstrap (default).** Creates its own project, item and RFQ and drives it
  to Clarifications, so the run needs nothing seeded and can be repeated.
  `--project` and `--item` reuse an existing project, and an existing item
  under it, instead of making new ones — for playing a second round against a
  package that is already in the roster. Neither guesses: `--item` is an id,
  not a description to match, because an item picked by resemblance is an RFQ
  raised against the wrong purchase.
* **The shortlist is free text unless the fixture says otherwise.** That is the
  deliberate default: the ADNOC export is untracked, so a mock linked to an
  *imported* vendor would only run on a machine that has it, and client
  approval reads as *not checked* on all three rows — the third state that
  column has, and worth seeing. A fixture whose vendor carries a `registry`
  block instead gets a bidder created in the registry from that block, and is
  invited by `vendor_id`, so the shortlist derives its snapshot from the
  registry rather than taking the script's word for it. Those bidders are
  invented, exactly as the free-text names are, and each one's `notes` says so
  in its first sentence — a registry row is more convincing than a typed name,
  which makes labelling it matter more, not less.
* **`--rfq <id>`.** Plays against an RFQ that is already at Clarifications with
  at least three included vendors. Vendor slots map to its shortlist by
  position, and query numbers continue from whatever the register already
  holds — which makes this the mode that checks numbering is `max + 1` and not
  `count + 1`, on an RFQ whose earlier queries have been answered or withdrawn.
  Its register must be **settled** first: the fixture's gate steps assert that
  the *only* thing outstanding is what the round itself created, so a
  pre-existing open query or draft addendum is refused up front rather than
  surfacing as a gate failure that blames the wrong step. The seeded
  `rfq_ruu02` is deliberately parked mid-round and will be refused for exactly
  that reason.

**`--until <ref>` stops after that step**, leaving the round half-played on
purpose: the queries on the register and nobody's answers against them, which
is what a real round looks like on the day it opens. The fixture's later
`expect_gate` steps are what make a full run an assertion, so a stopped run
proves less — it is a way to *stage* a round for someone to work through in the
browser, not a shorter version of the check. It says so when it stops.

**`--resume <rfq_id>` finishes one.** It reads the register back, matches each
stored record to the fixture ref that made it, skips every step whose effect is
already there, and plays the rest — so a `--until` run and a `--resume` of it
land on the same state a single full run would, assertions included. It wants
an *unsettled* register, which is the opposite of what `--rfq` wants and why
the two are separate flags rather than one with a mode.

Skipping is decided by reading state, not by counting steps, and a round that
is already finished is recognised as such and left alone. Both halves were
needed: every mutation here is idempotent on its own, but `expect_gate` is not
replayable once the round has moved on — those steps assert what the
Clarifications exit gate says, and an RFQ past it reports the *next* gate. A
resume that failed there would be calling a correctly-completed round a
regression.

The slot mapping on a resume is recovered from `raised_by_entry_id` on the
queries themselves rather than by position. That is the only sound way once
`also_invited` is in play, and it is why `--resume` works on a fixture that
`--rfq` refuses.

**Each bootstrap run leaves its project behind.** There is no route that deletes
an RFQ, and `delete_project` refuses a project that holds one, so the script
cannot tidy up after itself through the API — and it will not reach into
`workflow.json`, which is written only through `persistence.locked_update`. The
project is named "(mock round)" and its code carries the run's timestamp so the
leftovers are obvious in the roster; delete them by removing the store, not by
editing it.

Nothing here is real. The companies are the invented cast from
`workflow/seed_demo.py`; do not point this at anything but a development
instance.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from typing import Any

import requests

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE = pathlib.Path(__file__).resolve().with_suffix(".json")

OK = "  ok  "
FAIL = " FAIL "


class RoundFailed(Exception):
    """A step did not do what the fixture says it should.

    Raised rather than printed-and-continued: once the gate has answered wrong,
    every later step is being checked against a state the fixture no longer
    describes, and the second failure would say nothing the first did not.
    """


class Api:
    """Thin session wrapper. Keeps the cookie, and turns a non-2xx into a
    message that names the endpoint and the server's own detail — an infra
    check whose failure reads `HTTPError: 409` has wasted the trip."""

    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/")
        self.session = requests.Session()

    def login(self, email: str, password: str) -> str:
        body = self._call("POST", "/api/auth/login", {"email": email, "password": password})
        return body["email"]

    def whoami(self) -> str:
        return self._call("GET", "/api/auth/me")["email"]

    def _call(self, method: str, path: str, body: Any = None) -> Any:
        try:
            response = self.session.request(method, f"{self.base}{path}", json=body, timeout=30)
        except requests.ConnectionError as exc:
            raise RoundFailed(
                f"Could not reach {self.base}. Start the app with .\\run.ps1 first."
            ) from exc
        if response.status_code >= 400:
            raise RoundFailed(
                f"{method} {path} -> {response.status_code}: {_detail(response)}"
            )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def get(self, path: str) -> Any:
        return self._call("GET", path)

    def post(self, path: str, body: Any = None) -> Any:
        return self._call("POST", path, body if body is not None else {})

    def put(self, path: str, body: Any) -> Any:
        return self._call("PUT", path, body)


def _detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200]
    return str(payload.get("detail", payload))[:400]


def _say(mark: str, ref: str, message: str) -> None:
    print(f"[{mark}] {ref:<5} {message}")


# ---------------------------------------------------------------- bootstrap


def ensure_bidder(api: Api, registry: dict) -> str:
    """The registry id for this fixture bidder, creating the row if it is new.

    Matched on name, because that is the only field a fixture can state and a
    stored bidder is sure to have kept — ids are minted by the server. A second
    run of the same fixture therefore re-invites the same three companies
    rather than filling the registry with duplicates that differ only by id.

    It does not *update* a bidder that already exists. Editing one would make
    the script a writer of record for rows a person may have corrected by hand,
    and a mock that silently reverts somebody's edit is worse than one that is
    out of date.
    """
    existing = next(
        (b for b in api.get("/api/workflow/bidders")["bidders"]
         if b["name"] == registry["name"]),
        None,
    )
    if existing:
        _say(OK, "SET", f"bidder {existing['id']} ({registry['name']}) already registered")
        return existing["id"]
    bidder = api.post("/api/workflow/bidders", registry)
    _say(OK, "SET", f"bidder {bidder['id']} ({bidder['name']}) registered")
    return bidder["id"]


def find_bidder(api: Api, name: str) -> str:
    """The registry id for a bidder that must already exist.

    The counterpart to `ensure_bidder`, and deliberately not the same function
    with a flag. `also_invited` names companies quoted from a real approved
    vendor list, and the one thing that must never happen when such a name is
    absent is that the script invents a row to carry on with — that row would
    be a real company's name against a profile nobody recorded. Absent means
    the list has not been imported, and saying so is the whole job.
    """
    match = next(
        (b for b in api.get("/api/workflow/bidders")["bidders"] if b["name"] == name),
        None,
    )
    if match is None:
        raise RoundFailed(
            f"{name!r} is not in the registry. This name is quoted from a real "
            "approved vendor list and is never created here — import the list "
            "first with `python -m workflow.avl_db <xlsx>`."
        )
    return match["id"]


def shortlist_body(api: Api, vendor: dict) -> dict:
    """What to invite this vendor with.

    A `registry` block means the snapshot is the registry's to state: the route
    derives `vendor_name`, `prequal_status` and `scope_code_fit` from the bidder
    and ignores whatever is sent alongside, so sending them here would be
    writing three fields that are discarded and reading them back as though
    they had been honoured.
    """
    if vendor.get("registry"):
        return {"vendor_id": ensure_bidder(api, vendor["registry"]), "included": True}
    return {
        "vendor_name": vendor["name"],
        "prequal_status": vendor["prequal_status"],
        "scope_code_fit": vendor["scope_code_fit"],
        "included": True,
    }


def bootstrap(
    api: Api,
    spec: dict,
    vendors: list[dict],
    also_invited: list[str],
    project_id: str | None = None,
    item_id: str | None = None,
) -> tuple[str, dict[str, dict]]:
    """Create an RFQ and walk it to Clarifications, returning it and its slots.

    Returns the shortlist entry each vendor slot was given, because this is the
    one place that knows: `also_invited` puts rows on the shortlist that no
    slot names, so the positional mapping `--rfq` mode falls back on would
    quietly hand a slot the wrong entry.

    Every step here is a real route, so this half is itself a check on the
    stages before the one under test: a freeze that silently did nothing would
    surface as a refused transition out of Scoping rather than as a mystery
    three steps later.
    """
    tag = time.strftime("%m%d-%H%M%S")
    if project_id:
        project = api.get(f"/api/workflow/projects/{project_id}")["project"]
        _say(OK, "SET", f"reusing project {project['id']} ({project['code']})")
    else:
        project_spec = spec["project"]
        project = api.post("/api/workflow/projects", {
            **{k: v for k, v in project_spec.items() if k != "code"},
            "code": f"{project_spec['code']}-{tag}",
        })
        _say(OK, "SET", f"project {project['id']} ({project['code']})")

    if item_id:
        # Checked against this project's own items rather than fetched by id:
        # the RFQ route refuses an item from another project anyway, and a
        # refusal three calls later would not say which of the two ids was
        # wrong.
        items = api.get(f"/api/workflow/projects/{project['id']}/items")["items"]
        item = next((i for i in items if i["id"] == item_id), None)
        if item is None:
            raise RoundFailed(
                f"{item_id} is not an item of project {project['id']}. "
                f"It holds {', '.join(i['id'] for i in items) or 'no items'}."
            )
        _say(OK, "SET", f"reusing item {item['id']} ({item['item_type']})")
    else:
        item = api.post(f"/api/workflow/projects/{project['id']}/items", spec["item"])
        _say(OK, "SET", f"item {item['id']}")

    rfq = api.post("/api/workflow/rfqs", {
        "project_id": project["id"],
        "item_ids": [item["id"]],
        "reference": f"{spec['reference']}-{tag}",
        "package": spec["package"],
        "discipline": spec["discipline"],
        "value_estimate_aed": spec["value_estimate_aed"],
    })
    rfq_id = rfq["id"]
    _say(OK, "SET", f"RFQ {rfq_id} ({rfq['reference']}) at {rfq['stage']}")

    api.put(f"/api/workflow/rfqs/{rfq_id}/technical-package", spec["technical_package"])
    api.post(f"/api/workflow/rfqs/{rfq_id}/technical-package/freeze")
    api.post(f"/api/workflow/rfqs/{rfq_id}/transition", {"target": "Shortlisting"})
    _say(OK, "SET", "package frozen, RFQ at Shortlisting")

    # The entry each slot got, kept as it is created rather than matched back
    # off the shortlist afterwards. Position matching is what `--rfq` mode has
    # to do; here the ids are already in hand, and guessing at them again would
    # go wrong the moment the shortlist holds anyone the slots do not name —
    # which, with `also_invited`, it now does.
    entries = {}
    for vendor in vendors:
        entries[vendor["slot"]] = api.post(
            f"/api/workflow/rfqs/{rfq_id}/shortlist", shortlist_body(api, vendor)
        )

    # Invited, and that is all. These are real companies from the client's own
    # list, so they get a shortlist row — a record that somebody was asked to
    # bid — and no query, which would be a record of something they said.
    for name in also_invited:
        api.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", {
            "vendor_id": find_bidder(api, name), "included": True,
        })
    if also_invited:
        _say(OK, "SET", f"{len(also_invited)} vendors invited from the client's "
                        f"list, raising nothing: {', '.join(also_invited)}")

    # Approved after every vendor is on, never between two of them: adding one
    # revokes approval, so approving first would leave the RFQ looking signed
    # off against a shortlist it was not signed off against.
    api.post(f"/api/workflow/rfqs/{rfq_id}/shortlist/approve")
    api.put(f"/api/workflow/rfqs/{rfq_id}/tbe-template", {"criteria": spec["tbe_criteria"]})
    _say(OK, "SET", f"{len(vendors) + len(also_invited)} vendors shortlisted and "
                    "approved, TBE template set")

    api.post(f"/api/workflow/rfqs/{rfq_id}/transition", {"target": "Issued"})
    api.post(f"/api/workflow/rfqs/{rfq_id}/transition", {
        "target": "Clarifications",
        "reason": "Query round opened for the mock clarification exchange.",
    })
    _say(OK, "SET", "RFQ at Clarifications — the round starts here")
    return rfq_id, entries


def resolve_entries(api: Api, rfq_id: str, vendors: list[dict]) -> dict[str, dict]:
    """Map the fixture's vendor slots onto this RFQ's shortlist, by position.

    By position and not by name on purpose: in `--rfq` mode the shortlist holds
    whoever was actually invited, and a name match would fail on every RFQ but
    the one this fixture was written against.

    **Only `--rfq` mode calls this.** A bootstrap run knows which entry it gave
    each slot and says so directly — with `also_invited` on the shortlist there
    are more included entries than there are slots, and taking the first three
    by position would attribute the round's questions to whichever rows landed
    first.
    """
    payload = api.get(f"/api/workflow/rfqs/{rfq_id}")
    if payload["rfq"]["stage"] != "Clarifications":
        raise RoundFailed(
            f"{rfq_id} is at {payload['rfq']['stage']}, not Clarifications. "
            "Move it there, or drop --rfq and let the script bootstrap its own."
        )
    included = [e for e in payload["shortlist"] if e["included"]]
    if len(included) < len(vendors):
        raise RoundFailed(
            f"{rfq_id} has {len(included)} included shortlist entries; "
            f"the round needs {len(vendors)}."
        )

    # The fixture's gate steps assert that the only thing outstanding is what
    # this round created — G2 in particular asserts the reason no longer names
    # any open query. An RFQ carrying its own unfinished business fails that
    # step for a reason that has nothing to do with the code under test, so it
    # is refused here, where the message can say which records are in the way.
    outstanding = [q["number"] for q in payload["queries"] if q["state"] == "Open"]
    outstanding += [a["number"] for a in payload["addenda"] if a["draft"]]
    if outstanding:
        raise RoundFailed(
            f"{rfq_id} already has {', '.join(sorted(outstanding))} outstanding. "
            "The round asserts what the exit gate says, so it needs a settled "
            "register to start from: answer or withdraw each open query and "
            "issue or delete each draft addendum first, or drop --rfq and let "
            "the script bootstrap its own RFQ."
        )
    return {v["slot"]: included[i] for i, v in enumerate(vendors)}


def recover_state(
    api: Api, rfq_id: str, fixture: dict
) -> tuple[dict[str, dict], dict[str, dict], dict[str, dict]]:
    """Rebuild what a `--until` run left behind, from the register itself.

    Three things come back: which fixture ref is which stored query, the same
    for addenda, and which shortlist entry each vendor slot used.

    **The slot mapping is read off the queries, not guessed by position.** A
    stored query carries the `raised_by_entry_id` it was raised against, so the
    register is a record of the very mapping the first run made — which is the
    only sound way to recover it once `also_invited` has put rows on the
    shortlist that no slot names.

    Matching is on `expect_number`, which is sound here for the reason it is
    unsound in `--rfq` mode: a `--until` run bootstraps its own RFQ, so its
    register started empty and the numbers the fixture wrote are the numbers it
    got. A resume against an RFQ that already held queries would find nothing
    and try to raise them all again, which is why `--resume` says what it
    matched.
    """
    payload = api.get(f"/api/workflow/rfqs/{rfq_id}")
    queries_by_number = {q["number"]: q for q in payload["queries"]}
    addenda_by_number = {a["number"]: a for a in payload["addenda"]}

    queries: dict[str, dict] = {}
    addenda: dict[str, dict] = {}
    entries: dict[str, dict] = {}
    for step in fixture["exchange"]:
        if step["action"] == "raise_query":
            found = queries_by_number.get(step.get("expect_number"))
            if found:
                queries[step["ref"]] = found
                entries[step["vendor"]] = {"id": found["raised_by_entry_id"]}
        elif step["action"] == "draft_addendum":
            found = addenda_by_number.get(step.get("expect_number"))
            if found:
                addenda[step["ref"]] = found
    return queries, addenda, entries


# ------------------------------------------------------------------- steps


def _expect(condition: bool, ref: str, message: str) -> None:
    if not condition:
        raise RoundFailed(f"{ref}: {message}")


def _already_done(step: dict, queries: dict, addenda: dict) -> bool:
    """Whether the store already holds this step's effect.

    Only the steps that leave an identifiable record can be recognised, which
    is enough: the fixture raises before it answers, so a resume that skips the
    raises and the answers already given lands on the first thing left to do.
    Read off state rather than off a step counter, so re-running a resume is
    harmless — the second run finds everything done and changes nothing.
    """
    action = step["action"]
    if action == "raise_query":
        return step["ref"] in queries
    if action == "answer_query":
        # Anything but Open, not merely Answered. A query answered and then
        # withdrawn reads **Withdrawn** — withdrawal is checked before an
        # answer, which is the point of deriving state from three timestamps
        # rather than storing it — and the server refuses to answer it a
        # second time. Testing for "Answered" here made a resume of a
        # completed round die on the one query the fixture withdraws.
        return queries.get(step["query"], {}).get("state") not in (None, "Open")
    if action == "withdraw_query":
        return queries.get(step["query"], {}).get("state") == "Withdrawn"
    if action == "draft_addendum":
        return step["ref"] in addenda
    if action == "issue_addendum":
        return addenda.get(step["addendum"], {}).get("draft") is False
    # A gate reading and a transition are cheap and idempotent to re-check,
    # and re-checking them is the point of resuming rather than a cost of it.
    return False


def play(
    api: Api,
    rfq_id: str,
    fixture: dict,
    entries: dict[str, dict],
    until: str | None = None,
    queries: dict[str, dict] | None = None,
    addenda: dict[str, dict] | None = None,
) -> None:
    queries = dict(queries or {})   # fixture ref -> latest server payload
    addenda = dict(addenda or {})

    if until and until not in {s["ref"] for s in fixture["exchange"]}:
        raise RoundFailed(
            f"--until {until} names no step in this fixture. It has "
            f"{', '.join(s['ref'] for s in fixture['exchange'])}."
        )

    for step in fixture["exchange"]:
        ref, action = step["ref"], step["action"]

        if _already_done(step, queries, addenda):
            _say(OK, ref, "already on the record, skipped")
            if until and ref == until:
                break
            continue

        if action == "raise_query":
            if step["vendor"] not in entries:
                raise RoundFailed(
                    f"{ref}: no shortlist entry known for {step['vendor']}. On a "
                    "resume the slot mapping is read off the queries already "
                    "raised, so a slot that raised none cannot be placed — and "
                    "guessing at it by position is what would put this question "
                    "in the wrong company's mouth."
                )
            entry = entries[step["vendor"]]
            query = api.post(f"/api/workflow/rfqs/{rfq_id}/queries", {
                "raised_by_entry_id": entry["id"],
                "category": step["category"],
                "question": step["question"],
                "raised_on": step["raised_on"],
            })
            queries[ref] = query
            # Only checked when the register started empty. In --rfq mode the
            # sequence continues from what is already there, and asserting the
            # bootstrap number would fail on exactly the run that proves
            # numbering is max+1 rather than count+1.
            expected = step.get("expect_number")
            if expected and expected != query["number"]:
                _say(OK, ref, f"{query['number']} raised by {query['raised_by_name']} "
                              f"(continues an existing register; fixture wrote {expected})")
            else:
                _say(OK, ref, f"{query['number']} raised by {query['raised_by_name']}")

        elif action == "answer_query":
            target = queries[step["query"]]
            body: dict[str, Any] = {"answer": step["answer"]}
            if step.get("restricted_reason"):
                body["restricted_reason"] = step["restricted_reason"]
            query = api.post(
                f"/api/workflow/rfqs/{rfq_id}/queries/{target['id']}/answer", body
            )
            queries[step["query"]] = query
            _expect(query["state"] == "Answered", ref,
                    f"answered {query['number']} but it reads {query['state']}")
            restricted = step.get("restricted_reason") is not None
            _expect(query["circulated"] is not restricted, ref,
                    f"{query['number']} circulated={query['circulated']} with "
                    f"restricted_reason {'set' if restricted else 'absent'}")
            how = "restricted" if restricted else "circulated to the shortlist"
            _say(OK, ref, f"{query['number']} answered by {query['answered_by']}, {how}")

        elif action == "withdraw_query":
            target = queries[step["query"]]
            query = api.post(
                f"/api/workflow/rfqs/{rfq_id}/queries/{target['id']}/withdraw",
                {"reason": step["reason"]},
            )
            queries[step["query"]] = query
            _expect(query["state"] == step["expect_state"], ref,
                    f"{query['number']} reads {query['state']}, fixture expects "
                    f"{step['expect_state']} — an answered-then-withdrawn query "
                    "must not read as Answered, or the gate counts a dead "
                    "question as satisfied")
            _say(OK, ref, f"{query['number']} withdrawn and still reads "
                          f"{query['state']} despite carrying an answer")

        elif action == "draft_addendum":
            addendum = api.post(f"/api/workflow/rfqs/{rfq_id}/addenda", {
                "revision": step["revision"],
                "summary": step["summary"],
                "attachments": step["attachments"],
                "arising_from_query_ids": [queries[q]["id"] for q in step["arising_from"]],
                "bid_due_date": step["bid_due_date"],
            })
            addenda[ref] = addendum
            _expect(addendum["draft"], ref, f"{addendum['number']} is not a draft")
            _say(OK, ref, f"{addendum['number']} drafted, superseding "
                          f"{addendum['supersedes_revision']} at {addendum['revision']}")

        elif action == "issue_addendum":
            target = addenda[step["addendum"]]
            addendum = api.post(
                f"/api/workflow/rfqs/{rfq_id}/addenda/{target['id']}/issue"
            )
            addenda[step["addendum"]] = addendum
            _expect(not addendum["draft"], ref, f"{addendum['number']} still reads draft")
            due = api.get(f"/api/workflow/rfqs/{rfq_id}")["bid_due_date"]
            _expect(due == addendum["bid_due_date"], ref,
                    f"RFQ bid_due_date is {due}, issued addendum carries "
                    f"{addendum['bid_due_date']} — the RFQ's date is derived from "
                    "the latest issued addendum, never stored")
            _say(OK, ref, f"{addendum['number']} issued by {addendum['issued_by']}; "
                          f"bid due date now {due}")

        elif action == "expect_gate":
            gate = api.get(f"/api/workflow/rfqs/{rfq_id}")["gate"]
            reason = gate.get("reason") or ""
            _expect(gate["passed"] == step["passed"], ref,
                    f"gate passed={gate['passed']}, fixture expects "
                    f"{step['passed']}. Reason: {reason or '(none)'}")
            for needle in step.get("reason_contains", []):
                _expect(needle in reason, ref,
                        f"gate reason does not name {needle!r}: {reason}")
            for needle in step.get("reason_excludes", []):
                _expect(needle not in reason, ref,
                        f"gate reason still names {needle!r} after it was "
                        f"resolved: {reason}")
            _say(OK, ref, "gate open" if gate["passed"] else f"gate closed — {reason}")

        elif action == "transition":
            # Read the stage first, because a transition is the one step whose
            # retry is refused rather than absorbed: `TRANSITIONS` has no
            # self-edge, so replaying a move that already happened is a 409
            # naming an edge that does not exist. That is a confusing way for
            # an otherwise-complete resume to end.
            current = api.get(f"/api/workflow/rfqs/{rfq_id}")["rfq"]["stage"]
            if current == step["target"]:
                _say(OK, ref, f"already at {current}, skipped")
                if until and ref == until:
                    break
                continue
            rfq = api.post(f"/api/workflow/rfqs/{rfq_id}/transition", {
                "target": step["target"], "reason": step["reason"],
            })
            _expect(rfq["stage"] == step["target"], ref,
                    f"RFQ is at {rfq['stage']}, expected {step['target']}")
            _say(OK, ref, f"transitioned to {rfq['stage']}")

        else:  # pragma: no cover - a fixture typo, worth failing loudly
            raise RoundFailed(f"{ref}: unknown action {action!r}")

        if until and ref == until:
            remaining = [
                s["ref"] for s in fixture["exchange"]
                if fixture["exchange"].index(s) > fixture["exchange"].index(step)
            ]
            print(f"\nstopped after {ref} as asked. {len(remaining)} steps not played: "
                  f"{', '.join(remaining)}")
            # Said plainly, because the value of a full run is the expect_gate
            # steps and a stopped run has skipped some of them. A reader who
            # takes this for a pass has been told otherwise.
            print("This staged the round; it did not check it. Run without "
                  "--until for the assertions.")
            break

    summarise(api, rfq_id)


def summarise(api: Api, rfq_id: str) -> None:
    payload = api.get(f"/api/workflow/rfqs/{rfq_id}")
    approver = payload.get("client_approver") or "the client"
    print("\n--- shortlist as stored ---")
    for entry in payload["shortlist"]:
        # Three states, and None is not False: a vendor with no registry row
        # has had no check run, which is a different answer from failing one.
        approval = {
            True: f"on the {approver} list",
            False: f"NOT on the {approver} list",
            None: "not checked",
        }[entry["client_approved"]]
        print(f"  {entry['vendor_name'][:36]:<38} {approval}")
    print("\n--- register as stored ---")
    for query in payload["queries"]:
        flag = "" if query["circulated"] else "  [restricted]"
        print(f"  {query['number']:<8} {query['state']:<10} "
              f"{query['raised_by_name']}{flag}")
    for addendum in payload["addenda"]:
        state = "draft" if addendum["draft"] else f"issued by {addendum['issued_by']}"
        print(f"  {addendum['number']:<8} {state:<10} "
              f"{addendum['supersedes_revision']} -> {addendum['revision']}")
    print(f"\n  stage        {payload['rfq']['stage']}")
    print(f"  bid due      {payload['bid_due_date']}")
    print(f"  next gate    {'open' if payload['gate']['passed'] else 'closed'}")
    print(f"\nOpen it at http://localhost:5173/rfqs/{rfq_id}")


# -------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:8000",
                        help="Where the API is. Default http://localhost:8000.")
    parser.add_argument("--email", help="Sign in as this user. Omit when the API "
                                        "runs with AUTH_DISABLED=1 (run.ps1 -NoAuth).")
    parser.add_argument("--password")
    parser.add_argument("--rfq", help="Play against this existing RFQ instead of "
                                      "bootstrapping one. It must be at "
                                      "Clarifications with three included vendors.")
    parser.add_argument("--project", help="Raise the bootstrap RFQ inside this "
                                          "existing project instead of creating one.")
    parser.add_argument("--item", help="Cover this existing item, which must belong "
                                       "to --project. Without it the fixture's own "
                                       "item is created.")
    parser.add_argument("--resume", help="Finish a round this fixture already "
                                         "staged against this RFQ. Steps whose "
                                         "effect is already stored are skipped; "
                                         "unlike --rfq it wants an unsettled "
                                         "register, because that is what it is "
                                         "there to settle.")
    parser.add_argument("--until", help="Stop after this exchange step, e.g. Q6, "
                                        "leaving the round open. Stages a round to "
                                        "work through by hand; skips the gate "
                                        "assertions that make a full run a check.")
    parser.add_argument("--fixture", default=str(FIXTURE),
                        help="The round to play. Default tools/mock_clarification_round.json.")
    args = parser.parse_args(argv)

    fixture = json.loads(pathlib.Path(args.fixture).read_text(encoding="utf-8"))
    api = Api(args.base_url)

    try:
        if args.email:
            if not args.password:
                raise RoundFailed("--email needs --password.")
            who = api.login(args.email, args.password)
        else:
            # Not a convenience: a 401 here is the answer to "is the middleware
            # actually fail-closed", and saying so beats a bare 401 nine calls
            # into the round.
            try:
                who = api.whoami()
            except RoundFailed as exc:
                raise RoundFailed(
                    f"{exc}\nThe API is enforcing authentication, which is correct. "
                    "Pass --email/--password, or start it with .\\run.ps1 -NoAuth."
                ) from exc
        print(f"signed in as {who} against {api.base}\n")

        queries = addenda = None
        if args.resume:
            if args.rfq or args.project or args.item:
                raise RoundFailed("--resume names the RFQ to finish, so it takes "
                                  "neither --rfq nor --project/--item.")
            rfq_id = args.resume
            queries, addenda, entries = recover_state(api, rfq_id, fixture)
            if not queries and not addenda:
                raise RoundFailed(
                    f"Nothing of this fixture is stored against {rfq_id}. Resume "
                    "finishes a round this fixture staged; to start one, drop "
                    "--resume."
                )
            _say(OK, "SET", f"resuming {rfq_id}: {len(queries)} queries and "
                            f"{len(addenda)} addenda already on the record, "
                            f"across {len(entries)} vendor slots")

            # A finished round is recognised and left alone. Its mutations
            # would all skip, but its `expect_gate` steps would not: they
            # assert what the *Clarifications* exit gate says, and an RFQ that
            # has passed through it now reports the next gate instead. Failing
            # on that would be the script calling a correctly-completed round a
            # regression.
            finished_at = next(
                (s["target"] for s in reversed(fixture["exchange"])
                 if s["action"] == "transition"), None
            )
            stage = api.get(f"/api/workflow/rfqs/{rfq_id}")["rfq"]["stage"]
            if finished_at and stage == finished_at:
                print(f"\nThis round is already complete — {rfq_id} is at "
                      f"{stage}. Nothing to resume.")
                summarise(api, rfq_id)
                return 0
        elif args.rfq:
            if args.project or args.item:
                raise RoundFailed(
                    "--rfq plays against an RFQ that already exists, so it has a "
                    "project and items already. Drop --project/--item, or drop --rfq."
                )
            if fixture.get("also_invited"):
                # Refused rather than warned. This fixture separates the
                # vendors who raise questions from the ones who are only
                # invited, and that separation cannot be recovered from an
                # arbitrary shortlist by position — the failure would be a
                # real company recorded as asking a question it never asked,
                # which is the precise thing the separation exists to prevent.
                raise RoundFailed(
                    "This fixture carries `also_invited`, so its slots cannot be "
                    "mapped onto an existing shortlist by position without "
                    "risking attributing its questions to the wrong vendor. "
                    "Drop --rfq and let it bootstrap, or use a fixture without "
                    "`also_invited`."
                )
            rfq_id = args.rfq
            _say(OK, "SET", f"playing against existing RFQ {rfq_id}")
            entries = resolve_entries(api, rfq_id, fixture["vendors"])
        else:
            if args.item and not args.project:
                raise RoundFailed("--item needs --project: an item id alone does "
                                  "not say which project the RFQ belongs to.")
            rfq_id, entries = bootstrap(
                api, fixture["rfq"], fixture["vendors"],
                fixture.get("also_invited", []),
                project_id=args.project, item_id=args.item,
            )
        print()
        play(api, rfq_id, fixture, entries, until=args.until,
             queries=queries, addenda=addenda)
    except RoundFailed as exc:
        print(f"\n[{FAIL}] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
