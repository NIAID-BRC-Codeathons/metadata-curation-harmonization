"""Streaming JSONL ingestion and parameterized SQLite queries. No ORM required."""
import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

MAX_LINE = 8 * 1024 * 1024
MAX_FIELDS = 500
OPERATORS = {
    "contains": "contains", "eq": "equals", "ne": "does not equal",
    "gt": "greater than", "gte": "at least", "lt": "less than",
    "lte": "at most", "null": "is null", "missing": "is missing",
    "exists": "is present",
}


class DataError(ValueError):
    pass


def connect(path):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def initialize(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path)) as db, db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS datasets (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, source TEXT NOT NULL,
                imported_at TEXT NOT NULL, record_count INTEGER NOT NULL DEFAULT 0,
                fields TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY, dataset_id INTEGER NOT NULL REFERENCES datasets(id),
                line_number INTEGER NOT NULL, payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS records_dataset ON records(dataset_id, id);
            CREATE TABLE IF NOT EXISTS record_values (
                record_id INTEGER NOT NULL REFERENCES records(id),
                field TEXT NOT NULL, value TEXT, kind TEXT NOT NULL,
                PRIMARY KEY(record_id, field)
            );
            CREATE INDEX IF NOT EXISTS values_lookup ON record_values(field, value, record_id);
        """)


def flatten(obj, prefix="", depth=0):
    if depth > 30:
        raise DataError("Nested objects may be at most 30 levels deep.")
    for key, value in obj.items():
        # JSON Pointer escaping keeps literal dots, slashes, and nested keys distinct.
        path = prefix + "/" + key.replace("~", "~0").replace("/", "~1")
        if isinstance(value, dict) and value:
            yield from flatten(value, path, depth + 1)
        else:
            if value is None:
                kind, stored = "null", None
            elif isinstance(value, bool):
                kind, stored = "boolean", str(value).lower()
            elif isinstance(value, (int, float)):
                if isinstance(value, float) and not math.isfinite(value):
                    raise DataError("Numbers must be finite.")
                kind, stored = "number", str(value)
            elif isinstance(value, str):
                kind, stored = "text", value
            else:
                kind, stored = "json", json.dumps(value, ensure_ascii=False, allow_nan=False)
            yield path, stored, kind


def reject_constant(value):
    raise DataError(f"{value} is not a valid JSON number.")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DataError(f"Duplicate object key: {key!r}.")
        result[key] = value
    return result


def import_jsonl(db, stream, name, source):
    """All-or-nothing import; memory is bounded by one record and field metadata."""
    fields = {}
    count = 0
    line_no = 0
    with db:
        dataset_id = db.execute(
            "INSERT INTO datasets(name, source, imported_at) VALUES (?, ?, ?)",
            (name.strip()[:200] or source, source, datetime.now(timezone.utc).isoformat()),
        ).lastrowid
        while True:
            raw = stream.readline(MAX_LINE + 1)
            if not raw:
                break
            line_no += 1
            try:
                if len(raw) > MAX_LINE:
                    raise DataError("Record exceeds the 8 MiB line limit.")
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8-sig" if line_no == 1 else "utf-8")
                if not raw.strip():
                    continue
                obj = json.loads(raw, parse_constant=reject_constant, object_pairs_hook=unique_object)
                if not isinstance(obj, dict):
                    raise DataError("Each nonblank line must be a JSON object.")
                values = list(flatten(obj))
                for field, _, kind in values:
                    fields.setdefault(field, set()).add(kind)
                if len(fields) > MAX_FIELDS:
                    raise DataError(f"Dataset exceeds the {MAX_FIELDS}-field limit.")
                payload = json.dumps(obj, ensure_ascii=False, allow_nan=False)
                record_id = db.execute(
                    "INSERT INTO records(dataset_id, line_number, payload) VALUES (?, ?, ?)",
                    (dataset_id, line_no, payload),
                ).lastrowid
                db.executemany(
                    "INSERT INTO record_values(record_id, field, value, kind) VALUES (?, ?, ?, ?)",
                    ((record_id, field, value, kind) for field, value, kind in values),
                )
                count += 1
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise DataError(f"Line {line_no}: {exc}") from exc
        if not count:
            raise DataError("The file contains no JSON objects.")
        db.execute("UPDATE datasets SET record_count=?, fields=? WHERE id=?", (
            count, json.dumps({key: sorted(kinds) for key, kinds in fields.items()}), dataset_id,
        ))
    return dataset_id


def where_clause(dataset_id, fields, filters, search=""):
    clauses, params = ["r.dataset_id = ?"], [dataset_id]
    if search:
        clauses.append("EXISTS (SELECT 1 FROM record_values s WHERE s.record_id=r.id AND instr(lower(s.value), lower(?)) > 0)")
        params.append(search)
    if len(filters) > 20:
        raise DataError("Use at most 20 filters.")
    for field, op, value in filters:
        if field not in fields or op not in OPERATORS:
            raise DataError("Choose a valid field and filter operator.")
        sub = "SELECT 1 FROM record_values v WHERE v.record_id=r.id AND v.field=?"
        params.append(field)
        if op == "missing":
            clauses.append(f"NOT EXISTS ({sub})")
            continue
        if op == "null":
            sub += " AND v.kind='null'"
        elif op in {"gt", "gte", "lt", "lte"}:
            try:
                numeric = float(value)
                if not math.isfinite(numeric):
                    raise ValueError()
            except ValueError:
                raise DataError("Numeric comparisons require a finite number.") from None
            comparator = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
            sub += f" AND v.kind='number' AND CAST(v.value AS REAL) {comparator} ?"
            params.append(numeric)
        elif op == "contains":
            sub += " AND instr(lower(v.value), lower(?)) > 0"
            params.append(value)
        elif op in {"eq", "ne"}:
            comparator = "=" if op == "eq" else "<>"
            if set(fields[field]) <= {"number", "null"}:
                try:
                    value = float(value)
                    if not math.isfinite(value):
                        raise ValueError()
                except ValueError:
                    raise DataError("This field requires a finite number.") from None
                sub += f" AND v.kind='number' AND CAST(v.value AS REAL) {comparator} ?"
            else:
                sub += f" AND v.value {comparator} ?"
            params.append(value)
        clauses.append(f"EXISTS ({sub})")
    return " AND ".join(clauses), params


def order_clause(fields, sort, direction):
    if not sort:
        return "r.id ASC", []
    if sort not in fields or direction not in {"asc", "desc"}:
        raise DataError("Choose a valid sort column and direction.")
    expression = "CAST(v.value AS REAL)" if set(fields[sort]) <= {"number", "null"} else "v.value"
    return f"(SELECT {expression} FROM record_values v WHERE v.record_id=r.id AND v.field=?) {direction.upper()} NULLS LAST, r.id ASC", [sort]
