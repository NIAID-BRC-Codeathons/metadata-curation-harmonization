# Engine Explorer

A small, portable web application for exploring engine JSONL output in SQLite. A single Python process serves the UI and queries SQLite directly. There is no separate API service, JavaScript build, database server, or external asset dependency.

## Run on your Mac

Python 3.10 or newer is required. From this directory:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py serve
```

Open **http://127.0.0.1:8000**. Choose **Import JSONL** or **Explore sample data**. Stop the server with Ctrl+C. Imports persist in `data/explorer.sqlite3` across restarts. The `data/` directory is ignored by Git, so a fresh clone starts with an empty database. Import a JSONL file or load the included sample to populate it.

## Import engine output

Each nonblank line must be a UTF-8 JSON object. Field names and schemas can vary between records. For example:

```jsonl
{"id":"run-001","status":"completed","score":0.97,"metrics":{"runtime_s":4.2}}
{"id":"run-002","status":"failed","score":null,"metrics":{"runtime_s":1.1},"error":"timeout"}
```

Use the browser upload for files up to 128 MiB. For larger files, run:

```sh
python app.py import /path/to/output.jsonl --name "Engine run 01"
```

The CLI reads one line at a time, without loading the entire file into memory. Imports are atomic: malformed JSON, duplicate keys, nonfinite numbers, or non-object lines reject the entire file with a source line number. Blank lines are skipped. Each import creates a new dataset; repeating an import intentionally creates another dataset. Limits: 500 distinct leaf fields per dataset, 30 nested object levels, and 8 MiB per line. Arrays remain a single JSON-valued field. An empty object is a valid record.

To import the included 96-record synthetic materials-screening dataset:

```sh
python app.py demo
```

## Explore

- **Columns:** Select which discovered fields appear in the table. Nested fields display with a `›` separator.
- **Filters:** Click Filters, add one or more conditions, and apply. Conditions combine with AND. Text contains and global search are case-insensitive (SQLite's built-in case handling is primarily ASCII); equality is case-sensitive. Search checks stored field values, not field names. `%` and `_` are literal characters.
- **Numbers:** Greater/less comparisons accept finite numbers and match only numeric JSON fields. Equality on fields containing only numbers/nulls is numeric. Mixed-type fields use their stored text representation for equality and sorting. Numeric comparisons use SQLite floating-point precision; the full original values remain in the JSON payload.
- **Null vs. missing:** “Is null” matches an explicit JSON `null`; “is missing” matches an absent field; “is present” includes explicit nulls. “Does not equal” excludes missing and null values.
- **Sort and paginate:** Click a column heading to sort; click again to reverse. Numeric-only columns sort numerically, others as text, with missing/null values last. Pages contain 25, 50, or 100 records.
- **Details:** Click View on a row for all fields, full JSON, source file, import timestamp, and source line. Back to records preserves the view.
- **Export:** Export JSONL downloads all matching records in the selected sort order, across all pages, retaining every field regardless of visible columns. Record details also offer an individual JSON download.
- **Saved views:** Bookmark or share the current URL to retain columns, filters, sorting, and page. Dataset IDs are specific to the database being used.

## Database and code layout

`app.py` contains Flask routes and the CLI. `database.py` contains ingestion and parameterized SQL. `templates/` contains server-rendered HTML and `static/` contains local CSS and a small amount of JavaScript. Edit `templates/detail.html` when you decide what specialized record details should show.

The SQLite schema is generic:

- `datasets`: source filename, import timestamp, record count, and discovered fields/types.
- `records`: dataset ID, original line number, and complete JSON object content.
- `record_values`: flattened leaf fields, values, and types for filtering/sorting, indexed by record/field and field/value.

Field identifiers use escaped JSON Pointers internally, so nested keys and literal dots, slashes, quotes, or tildes do not collide. JSON content is preserved, but original whitespace/formatting is normalized. Each database connection enables foreign keys. The app uses SQLite's default rollback journal and a 30-second busy timeout; concurrent imports may cause readers/writers to wait. A single very large import is best done before opening the UI.

Use another database path with the global flag (before the subcommand) or environment variable:

```sh
python app.py --db /path/to/engine.sqlite3 import output.jsonl
python app.py --db /path/to/engine.sqlite3 serve --port 8080
# Or: export EXPLORER_DB=/path/to/engine.sqlite3
```

The database must use this application's schema. This version imports JSONL; it does not discover arbitrary pre-existing SQLite table schemas. Filtering/searching arbitrary dynamic fields can require scans; for very large datasets, benchmark real workloads and add indexes or dedicated columns for frequently queried fields.

## Move to Linux / Argonne infrastructure

Copy this source directory (excluding `.venv/`) to the target machine, create a new virtual environment there, and run the same installation/start commands. Waitress serves the application on both macOS and Linux; no development server is needed. Keep the SQLite database on a local persistent disk on the host running the app. To move existing data, stop the app and any imports before copying the database file, or use SQLite's backup mechanism for a live database.

For personal access on an allocated machine, keep the server bound to loopback and use an SSH tunnel from your laptop:

```sh
# On the target machine, within its permitted allocation/service environment:
python app.py --db /local/persistent/path/explorer.sqlite3 serve --port 8000

# On your laptop, adapting hostname and SSH routing to your environment:
ssh -L 8000:127.0.0.1:8000 your-user@your-host
```

Open http://127.0.0.1:8000 on your laptop. Actual scheduling, hostname, network access, and storage placement depend on the Argonne system you use.

For a shared deployment, run behind your institution's authenticated HTTPS reverse proxy and bind with `--host 0.0.0.0` only when that network exposure is intended. This app has no user accounts or dataset-level access controls. Set a stable `EXPLORER_SECRET` environment variable if sessions should survive restarts; without it, the app generates a new secret and open forms must be reloaded after restarting. It has no outbound data calls and does not fetch fonts or scripts from CDNs.

An optional container is included:

```sh
docker build -t engine-explorer .
docker run --rm -p 127.0.0.1:8000:8000 -v engine-data:/data engine-explorer
```

Container use is optional; choose the Python environment method if it fits the infrastructure better.

## Verify

```sh
python -m unittest discover -s tests -v
```

Tests cover import rollback, malformed input, UTF-8/BOM handling, nested/unusual keys, filtering, sorting, pagination, dataset isolation, full-content exports, HTML escaping, and form CSRF protection.
