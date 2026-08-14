"""Load an Approved Vendor List export into `<ROOT>/bidders.db`.

    python -m workflow.avl_db --avl "data/bidders_details/ADNOC ….xlsx"
    python -m workflow.avl_db --astra "data/bidders_details/… subset ….xlsx"
    python -m workflow.avl_db --from-document      # a pre-move workflow.json

The registry is reference data: it comes from a client's export and is replaced
wholesale when a newer export arrives. This is the command that does that, and
it is deliberately separate from `workflow.seed_demo` — that one builds a whole
demonstration store and replaces projects, items and RFQs along with it, which
is not what somebody refreshing the vendor list wants.

`--from-document` is the one-way migration: it reads the `bidders` key out of a
`workflow.json` written before the registry moved and puts it in the database.
Running it twice is harmless — the second run reads no key and says so.

`--astra` records our own approval against vendors the registry already holds.
It creates nobody: that file says who approved whom, not who exists, and a
vendor number in it with no bidder behind it is reported rather than invented.
It replaces `avl_import.astra_approves`, which derived the same field from a
hash so a demo had something to show — a real list and a fabricated one are
indistinguishable on screen, which is why the fabricated one goes.

Nothing here embellishes. `avl_import.parse_avl` leaves every field the sheet
does not carry empty, and `astra_subset` is deliberately not exposed: the Astra
overlay is invented, and a command whose job is "load the client's real list"
should not be able to mix fabricated approvals into it.
"""
import argparse
import json
import os
import sys

from procurement.store import layout
from workflow import astra_import, bidder_db, persistence
from workflow.avl_import import parse_avl
from workflow.models.bidder import ASTRA, Bidder


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m workflow.avl_db",
        description="Load an Approved Vendor List into <ROOT>/bidders.db.",
    )
    parser.add_argument(
        "--root",
        default=os.environ.get("PROCUREMENT_PROJECTS_ROOT", "projects"),
        help="the store root holding bidders.db (default: %(default)s)",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--avl", metavar="XLSX", help="an ADNOC AVL export to load")
    source.add_argument(
        "--from-document",
        action="store_true",
        help="migrate the bidders key out of an existing workflow.json",
    )
    source.add_argument(
        "--astra",
        metavar="XLSX",
        help="a vendor list of who Astra has approved; records that approval "
             "against the bidders already in the registry and creates none",
    )
    args = parser.parse_args(argv)

    if args.astra:
        if not os.path.exists(args.astra):
            print(f"No such vendor list: {args.astra}", file=sys.stderr)
            return 1
        numbers = astra_import.parse_vendor_numbers(args.astra)
        ids = [f"bdr_{n}" for n in numbers]
        recorded = bidder_db.add_approval(args.root, ids, ASTRA)
        print(
            f"Read {len(numbers)} vendors from {args.astra}; recorded {ASTRA} "
            f"approval against {recorded} of them."
        )
        if recorded < len(numbers):
            # Named rather than counted: a vendor number in this file with no
            # bidder behind it usually means the two exports are of different
            # vintages, and that is worth seeing rather than absorbing.
            missing = sorted(set(numbers) - {i.removeprefix("bdr_") for i in ids[:recorded]})
            print(
                f"{len(numbers) - recorded} were not in the registry and were "
                f"skipped — no bidder is created from this file. First few: "
                f"{', '.join(missing[:5])}",
                file=sys.stderr,
            )
        return 0

    if args.avl:
        if not os.path.exists(args.avl):
            print(f"No such Approved Vendor List: {args.avl}", file=sys.stderr)
            return 1
        bidders = parse_avl(args.avl)
        source_name = args.avl
    else:
        path = persistence.workflow_path(args.root)
        if not os.path.exists(path):
            print(f"No such document: {path}", file=sys.stderr)
            return 1
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
        records = doc.get("bidders")
        if not records:
            print(
                f"{path} carries no bidders key — nothing to migrate. The "
                f"database already holds {bidder_db.count(args.root)} bidders.",
                file=sys.stderr,
            )
            return 0
        bidders = [Bidder(**r) for r in records]
        source_name = path

    written = bidder_db.replace_all(args.root, bidders)
    print(
        f"Wrote {written} bidders from {source_name} to "
        f"{bidder_db.db_path(args.root)}."
    )
    if _drop_bidders_key(args.root):
        # Not tidiness. A document that still carries the key wins over the
        # database on load — that is what makes the migration safe — so leaving
        # it there would mean this command appeared to work and changed
        # nothing, with two copies of the registry disagreeing from then on.
        print(f"Dropped the stale bidders key from {persistence.workflow_path(args.root)}.")
    return 0


def _drop_bidders_key(root: str) -> bool:
    """Remove `bidders` from the document, if it is still there."""
    path = persistence.workflow_path(root)
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8") as handle:
        doc = json.load(handle)
    if "bidders" not in doc:
        return False
    del doc["bidders"]
    layout.atomic_write_json(path, doc)
    return True


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
