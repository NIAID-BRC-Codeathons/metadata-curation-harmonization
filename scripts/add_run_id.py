"""Prepend a `run_id` field to every record in each run's proposals.jsonl.

The value is the name of the parent folder (e.g. ``data.v1.5kcurated.work10``).
Each file is rewritten in place, preserving JSONL format and key order, with
``run_id`` as the first attribute of every object.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "data" / "clays"
FILENAME = "proposals.jsonl"


def add_run_id(path: Path) -> int:
    run_id = path.parent.name
    records = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            record.pop("run_id", None)
            records.append({"run_id": run_id, **record})

    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    tmp.replace(path)
    return len(records)


def main() -> None:
    for path in sorted(ROOT.glob(f"*/{FILENAME}")):
        n = add_run_id(path)
        print(f"{path.parent.name}: {n} rows updated")


if __name__ == "__main__":
    main()
