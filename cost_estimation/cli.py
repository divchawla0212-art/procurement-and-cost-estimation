import argparse
import json
import sys
from shared.llm.factory import get_client
from cost_estimation.config.loader import load_config
from cost_estimation.ingestion.extractor import ingest_directory
from cost_estimation.models.schema import CostDataset


def run_ingest(root: str, out_path: str) -> CostDataset:
    client = get_client()
    config = load_config()
    dataset = ingest_directory(root, client, config)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(dataset.model_dump(), fh, indent=2, default=str)
    counts = {"ok": 0, "discrepancies": 0, "failed": 0}
    for doc in dataset.documents:
        counts[doc.extraction_status] = counts.get(doc.extraction_status, 0) + 1
    print(f"Documents: {counts['ok']} ok, {counts['discrepancies']} discrepancies, {counts['failed']} failed")
    return dataset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cost-est")
    sub = parser.add_subparsers(dest="command", required=True)
    ing = sub.add_parser("ingest")
    ing.add_argument("root")
    ing.add_argument("--out", default="cost_dataset.json")
    args = parser.parse_args(argv)
    if args.command == "ingest":
        try:
            ds = run_ingest(args.root, args.out)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"Ingested {len(ds.work_packages)} work packages -> {args.out}")
        return 0
    return 1
