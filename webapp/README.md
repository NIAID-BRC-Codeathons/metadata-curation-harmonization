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

The CLI reads one line at a time, without loading the entire file into memory. Imports are atomic: malformed JSON, duplicate keys, nonfinite numbers, or non-object lines reject the entire file with a source line number. Blank lines are skipped. Each import creates a new dataset; repeating an import intentionally creates another dataset. Limits: 500 distinct leaf fields per dataset, 30 nested object levels, and 64 MiB per line by default. Set `EXPLORER_MAX_RECORD_MIB` to a positive integer before starting the server or CLI to change the per-record limit. Arrays remain a single JSON-valued field. An empty object is a valid record.

To import the included 96-record synthetic materials-screening dataset:

```sh
python app.py demo
```

## Explore

### Import directly from NCBI

Click **Import from NCBI** beside **Import JSONL** on the welcome or dataset page. The app first downloads [combined.v2.jsonl.gz](https://ftp.ncbi.nlm.nih.gov/pub/datasets/.argonne/combined.v2.jsonl.gz) to a local cache, then streams decompression from that file into SQLite. No manual download, extraction, or additional dependency is needed. Separating the stages prevents slow parsing and database writes from holding up the network connection.

The import runs in a background thread in the existing server process. Its status page refreshes every three seconds and shows the download and local import as separate stages, with compressed bytes received and records processed. A completed download does not mean the database import is complete. Choose **Explore imported records** when it finishes. Only one NCBI import runs at a time; clicking the button again while it runs returns to its status page. After completion, clicking again creates a new dataset.

The v2 file is about 69 MB compressed when checked (the original was about 2.6 GB). The archive is retained in `data/ncbi-cache/combined.v2.jsonl.gz` (or `ncbi-cache/` beside a custom database path). No standalone decompressed JSONL file is written. Allow space for the archive, database, indexes, and SQLite transaction files; the final database can be substantially larger than the decompressed JSON too. Network reads have a 60-second inactivity timeout; the whole import is not limited to 60 seconds or the browser's 128 MiB upload limit.

Interrupted network transfers get up to four attempts with short backoff delays. Saved `.part` bytes survive failures and server restarts. Cache files are named for their source archive, so v2 cannot reuse the original archive’s downloaded bytes. On retry the app checks the remote size and ETag or Last-Modified value, then sends a Range request with If-Range to resume an unchanged version. Invalid ranges are rejected; a server returning a full file instead causes a fresh download, never an append. Without a version validator the downloader starts from zero. Complete cached archives are reused only if remote metadata still matches. A gzip integrity failure invalidates the cache for the next attempt. When no import is running, deleting `data/ncbi-cache/` frees the cached download without deleting imported datasets.

Keep the app process running until completion. Leaving the page does not stop the import. Restarting the server interrupts unfinished work: partial downloads remain resumable, but incomplete SQLite imports roll back and start over from the retained archive. Status history is kept in memory (the ten most recent jobs), while completed datasets and cached downloads persist. No database import starts until the entire advertised download has been saved. Gzip or JSONL errors roll back the partial dataset and appear on the status page. Existing JSONL field/depth/line-size limits still apply. The server needs HTTPS access to `ftp.ncbi.nlm.nih.gov`, including for the remote metadata check when reusing a cache.

SQLite supports databases much larger than 54 GB ([SQLite limits](https://www.sqlite.org/limits.html)), but this generic schema is not optimized for very large nested data. It stores full JSON plus flattened values and a value index. A measured 17,872,897-byte record occupied 57,151,488 bytes in the database, with a further 57,486,392-byte WAL during the test. This single-record result is not a full-dataset estimate. Long atomic imports can need substantial temporary disk space, and filters over unindexed expressions can scan many records. Selective indexing, avoiding duplicate indexing of large arrays, and staged batch ingestion would be future optimizations for this scale.

### Diagnose an unsuccessful NCBI import

The status page shows **Error details**, the import ID, and the server log location. All exceptions—including download timeouts, gzip failures, and invalid JSONL—are logged with their full traceback to both the server terminal and `ncbi-import.log` beside the SQLite database (by default `data/ncbi-import.log`). Logs include the source URL, response metadata, elapsed time, compressed bytes received, and the last reported record count. Progress is logged approximately every 30 seconds while the worker is making progress. Record counts can lag by up to 999 records; JSONL validation errors include the exact line number. Files rotate at 5 MiB, keeping three older logs. Logs survive server restarts even though the status pages do not.

From `webapp/`, watch the log during an import or inspect a failure:

```sh
tail -f data/ncbi-import.log
tail -n 100 data/ncbi-import.log
```

Keep the **Error details** text or the traceback and import ID when reporting a failure. The work happens in Python on the server, so the browser console generally has no download error to show. A status percentage alone cannot distinguish a network interruption from a gzip or record-validation failure.

The previous 8 MiB record limit rejected valid data from the original `combined.jsonl.gz` archive: line 6,746 of the archive examined on September 17, 2026 was 17,872,897 bytes because of its `sra-experiment` content. The default is now 64 MiB. If a later record is larger still, increase the limit when starting the app, for example:

```sh
EXPLORER_MAX_RECORD_MIB=128 python app.py serve
```

This is a per-record limit, separate from the browser upload limit. Parsing and indexing an individual record can require several times its JSON size in memory. The full archive has not yet been validated end to end; other schema or size limits may surface on subsequent records.

### Table and record controls

The house-shaped **Home** link at the top of the sidebar returns to the home route (the latest dataset when available). The adjacent moon/sun button switches between light and dark mode. The app initially follows your system theme and remembers an explicit choice in this browser when local storage is available.

- **Columns:** Select which discovered fields appear in the table, or click **Select all** / **Deselect all** to check or uncheck every column at once (including Comments). Use **Search columns** to filter the list by column name as you type (case-insensitive); clearing the search restores the full list. Searching preserves your selections; both bulk buttons include fields hidden by the search. Applying with no columns selected leaves only row numbers and Details links. You can uncheck individual fields before clicking **Apply columns**. Nested fields display with a `›` separator.
- **Comments:** An editable Comments column appears by default at the far right of the table. Enter up to 10,000 characters per row and click its **Save** button; the status confirms when it is saved. You can hide Comments in the Columns dialog without deleting annotations. Saved comments persist in SQLite and appear on record detail pages. Export JSONL and individual record downloads include saved nonempty comments as a top-level `comments` field, even when the column is hidden. If an original field already uses that name, the app uses the first unused name (`comments_2`, `comments_3`, etc.) consistently across the dataset, displayed below the table. Original fields are never overwritten; rows without a saved comment export unchanged. Clear the text and Save to remove an annotation. Comments are separate annotations and are not included in field filtering, search, or sorting.
- **Delete datasets:** Open a dataset and choose **Delete dataset** beside Export JSONL. The confirmation dialog names the dataset and record count; **Cancel** keeps it. Confirming permanently removes its records, indexed values, and saved comments from SQLite. Original source files and cached NCBI downloads are retained.
- **Filters:** Click Filters, add one or more conditions, and apply. Conditions combine with AND. Text contains and global search are case-insensitive (SQLite's built-in case handling is primarily ASCII); equality is case-sensitive. Search checks stored field values, not field names. `%` and `_` are literal characters.
- **Numbers:** Greater/less comparisons accept finite numbers and match only numeric JSON fields. Equality on fields containing only numbers/nulls is numeric. Mixed-type fields use their stored text representation for equality and sorting. Numeric comparisons use SQLite floating-point precision; the full original values remain in the JSON payload.
- **Null vs. missing:** “Is null” matches an explicit JSON `null`; “is missing” matches an absent field; “is present” includes explicit nulls. “Does not equal” excludes missing and null values.
- **Sort and paginate:** Click a column heading to sort; click again to reverse. Numeric-only columns sort numerically, others as text, with missing/null values last. Pages contain 25, 50, or 100 records.
- **Details:** Click View on a row for all fields, full JSON, source file, import timestamp, and source line. Back to records preserves the view.
- **Export:** Export JSONL downloads all matching records in the selected sort order, across all pages, retaining every field regardless of visible columns. Record details also offer an individual JSON download.
- **Saved views:** Bookmark or share the current URL to retain columns, filters, sorting, and page. Dataset IDs are specific to the database being used.

## Database and code layout

`app.py` contains Flask routes and the CLI. `database.py` contains ingestion and parameterized SQL. `ncbi_download.py` handles cached downloads and resume; `ncbi_import.py` runs the background job and logs its progress. `templates/` contains server-rendered HTML and `static/` contains local CSS and a small amount of JavaScript. Edit `templates/detail.html` when you decide what specialized record details should show.

The SQLite schema is generic:

- `datasets`: source filename, import timestamp, record count, and discovered fields/types.
- `records`: dataset ID, original line number, and complete JSON object content.
- `record_values`: flattened leaf fields, values, and types for filtering/sorting, indexed by record/field and field/value.
- `record_comments`: saved per-record annotations, removed with their records. This table is added automatically when the app starts against an existing database; no reimport is required.

Field identifiers use escaped JSON Pointers internally, so nested keys and literal dots, slashes, quotes, or tildes do not collide. JSON content is preserved, but original whitespace/formatting is normalized. Each database connection enables foreign keys. The app uses SQLite WAL journaling so existing datasets and import status remain readable during long imports, with a 30-second busy timeout. SQLite still allows only one writer at a time; wait for a large import to finish before starting another upload or CLI import.

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

For a shared deployment, run behind your institution's authenticated HTTPS reverse proxy and bind with `--host 0.0.0.0` only when that network exposure is intended. This app has no user accounts or dataset-level access controls. Set a stable `EXPLORER_SECRET` environment variable if sessions should survive restarts; without it, the app generates a new secret and open forms must be reloaded after restarting. It only downloads external data when you request an NCBI import and does not fetch fonts or scripts from CDNs. Use one app process (the default Waitress setup) so the background import status and concurrency guard are shared by all requests.

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
