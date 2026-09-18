import argparse
import hmac
import json
import os
import secrets
import sqlite3
from pathlib import Path
from urllib.parse import urlencode

from flask import Flask, Response, jsonify, abort, flash, g, redirect, render_template, request, session, url_for

import database
from ncbi_import import ImportJobs, NCBI_URL, NCBI_FILENAME

ROOT = Path(__file__).resolve().parent
COMMENTS_COLUMN = "__comments"
MAX_COMMENT_LENGTH = 10000


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        DATABASE=os.environ.get("EXPLORER_DB", str(ROOT / "data" / "explorer.sqlite3")),
        SECRET_KEY=os.environ.get("EXPLORER_SECRET") or secrets.token_hex(32),
        MAX_CONTENT_LENGTH=128 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if config:
        app.config.update(config)
    database.initialize(app.config["DATABASE"])
    ncbi_jobs = ImportJobs(app.config["DATABASE"])
    app.extensions["ncbi_imports"] = ncbi_jobs

    def db():
        if "db" not in g:
            g.db = database.connect(app.config["DATABASE"])
        return g.db

    @app.teardown_appcontext
    def close_db(error=None):
        if "db" in g:
            g.db.close()

    @app.before_request
    def csrf_protection():
        session.setdefault("csrf", secrets.token_hex(24))
        if request.method == "POST" and not hmac.compare_digest(session["csrf"], request.form.get("csrf", "")):
            abort(400, "This form expired. Reload the page and try again.")

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'; base-uri 'self'"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.context_processor
    def shared():
        datasets = db().execute("SELECT * FROM datasets ORDER BY id DESC").fetchall()
        return dict(datasets=datasets, total_records=sum(d["record_count"] for d in datasets), operators=database.OPERATORS, ncbi_filename=NCBI_FILENAME, comments_column=COMMENTS_COLUMN)

    @app.template_filter("field_label")
    def field_label(value):
        return " › ".join(part.replace("~1", "/").replace("~0", "~") or '(empty key)' for part in value.split("/")[1:])

    @app.template_filter("number")
    def number(value):
        return f"{value:,}"

    def get_dataset(dataset_id):
        row = db().execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()
        if row is None:
            abort(404, "Dataset not found.")
        return row, json.loads(row["fields"])

    def query_state(fields):
        f, op, v = (request.args.getlist(key) for key in ("f", "op", "v"))
        if not len(f) == len(op) == len(v):
            raise database.DataError("Incomplete filter. Please choose a field, operator, and value.")
        filters = list(zip(f, op, v))
        columns = list(dict.fromkeys(c for c in request.args.getlist("col") if c in fields or c == COMMENTS_COLUMN))
        if not columns and request.args.get("columns_set") != "1":
            columns = list(fields)[:6] + [COMMENTS_COLUMN]
        return filters, columns

    @app.get("/")
    def index():
        latest = db().execute("SELECT id FROM datasets ORDER BY id DESC LIMIT 1").fetchone()
        if latest:
            return redirect(url_for("explore", dataset_id=latest["id"]))
        return render_template("empty.html", active_id=None)

    @app.post("/import")
    def upload():
        uploaded = request.files.get("file")
        if not uploaded or not uploaded.filename:
            raise database.DataError("Choose a JSONL file to import.")
        source = uploaded.filename.replace("\\", "/").rsplit("/", 1)[-1][:200]
        dataset_id = database.import_jsonl(db(), uploaded.stream, request.form.get("name", "") or source, source)
        flash("Import complete. Your records are ready to explore.", "success")
        return redirect(url_for("explore", dataset_id=dataset_id))

    @app.post("/demo")
    def demo():
        with (ROOT / "examples" / "engine-output.jsonl").open("rb") as stream:
            dataset_id = database.import_jsonl(db(), stream, "Materials screening · sample", "engine-output.jsonl")
        flash("Sample data loaded. Import your own JSONL whenever you’re ready.", "success")
        return redirect(url_for("explore", dataset_id=dataset_id))

    @app.post("/import/ncbi")
    def import_ncbi():
        job_id = ncbi_jobs.start()
        return redirect(url_for("ncbi_status", job_id=job_id), code=303)

    @app.get("/imports/ncbi/<job_id>")
    def ncbi_status(job_id):
        job = ncbi_jobs.snapshot(job_id)
        if job is None:
            abort(404, "This import status is no longer available. If the server restarted, check the dataset list for a completed import or start again.")
        response = app.make_response(render_template("ncbi_status.html", job=job, source=NCBI_URL, active_id=None))
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/datasets/<int:dataset_id>/delete")
    def delete_dataset(dataset_id):
        dataset, _ = get_dataset(dataset_id)
        if request.form.get("confirm") != "delete":
            abort(400, "Confirm dataset deletion before continuing.")
        with db():
            db().execute("DELETE FROM record_values WHERE record_id IN (SELECT id FROM records WHERE dataset_id=?)", (dataset_id,))
            db().execute("DELETE FROM records WHERE dataset_id=?", (dataset_id,))
            db().execute("DELETE FROM datasets WHERE id=?", (dataset_id,))
        flash(f'Deleted dataset “{dataset["name"]}”.', "success")
        return redirect(url_for("index"), code=303)

    @app.post("/datasets/<int:dataset_id>/records/<int:record_id>/comment")
    def save_comment(dataset_id, record_id):
        if db().execute("SELECT 1 FROM records WHERE id=? AND dataset_id=?", (record_id, dataset_id)).fetchone() is None:
            abort(404, "Record not found in this dataset.")
        comment = request.form.get("comment", "")
        if len(comment) > MAX_COMMENT_LENGTH:
            abort(400, "Comments must be 10,000 characters or fewer.")
        with db():
            if comment:
                db().execute("INSERT INTO record_comments(record_id, comment) VALUES (?, ?) ON CONFLICT(record_id) DO UPDATE SET comment=excluded.comment", (record_id, comment))
            else:
                db().execute("DELETE FROM record_comments WHERE record_id=?", (record_id,))
        if request.headers.get("Accept") == "application/json":
            return jsonify(saved=True)
        flash("Comment saved.", "success")
        target = url_for("explore", dataset_id=dataset_id)
        if request.query_string:
            target += "?" + request.query_string.decode()
        return redirect(target, code=303)

    def comment_export_key(fields):
        # Use one collision-free top-level key for the entire dataset.
        keys = {field.split("/")[1].replace("~1", "/").replace("~0", "~") for field in fields}
        key = "comments"
        suffix = 2
        while key in keys:
            key = f"comments_{suffix}"
            suffix += 1
        return key

    def annotated_payload(row, key):
        if row["comment"] is None:
            return row["payload"]
        obj = json.loads(row["payload"])
        obj[key] = row["comment"]
        return json.dumps(obj, ensure_ascii=False)

    @app.get("/datasets/<int:dataset_id>")
    def explore(dataset_id):
        dataset, fields = get_dataset(dataset_id)
        filters, columns = query_state(fields)
        search = request.args.get("q", "").strip()
        sort, direction = request.args.get("sort", ""), request.args.get("direction", "asc")
        where, params = database.where_clause(dataset_id, fields, filters, search)
        order, order_params = database.order_clause(fields, sort, direction)
        try:
            page = max(1, int(request.args.get("page", 1)))
            page_size = int(request.args.get("size", 25))
        except ValueError:
            raise database.DataError("Page and page size must be whole numbers.") from None
        if page_size not in {25, 50, 100}:
            page_size = 25
        count = db().execute(f"SELECT count(*) FROM records r WHERE {where}", params).fetchone()[0]
        pages = max(1, (count + page_size - 1) // page_size)
        page = min(page, pages)
        rows = db().execute(f"SELECT r.*, c.comment FROM records r LEFT JOIN record_comments c ON c.record_id=r.id WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?", params + order_params + [page_size, (page - 1) * page_size]).fetchall()
        records = [{"id": row["id"], "line": row["line_number"], "comment": row["comment"] or "", "values": {key: (value, kind) for key, value, kind in database.flatten(json.loads(row["payload"]))}} for row in rows]

        def query_url(**changes):
            args = request.args.to_dict(flat=False)
            for key, value in changes.items():
                args[key] = [str(value)]
            return url_for("explore", dataset_id=dataset_id) + "?" + urlencode(args, doseq=True)

        return render_template("explore.html", active_id=dataset_id, dataset=dataset, fields=fields, columns=columns, data_columns=[c for c in columns if c != COMMENTS_COLUMN], show_comments=COMMENTS_COLUMN in columns, comment_export_key=comment_export_key(fields), filters=filters, search=search, sort=sort, direction=direction, records=records, count=count, page=page, pages=pages, page_size=page_size, query_url=query_url, query_string=request.query_string.decode(), first=(page-1)*page_size+1 if count else 0, last=min(page*page_size, count))

    @app.get("/datasets/<int:dataset_id>/records/<int:record_id>")
    def detail(dataset_id, record_id):
        dataset, _ = get_dataset(dataset_id)
        record = db().execute("SELECT r.*, c.comment FROM records r LEFT JOIN record_comments c ON c.record_id=r.id WHERE r.id=? AND r.dataset_id=?", (record_id, dataset_id)).fetchone()
        if record is None:
            abort(404, "Record not found in this dataset.")
        obj = json.loads(record["payload"])
        back = url_for("explore", dataset_id=dataset_id)
        if request.query_string:
            back += "?" + request.query_string.decode()
        return render_template("detail.html", active_id=dataset_id, dataset=dataset, record=record, values=list(database.flatten(obj)), raw=json.dumps(obj, indent=2, ensure_ascii=False), back=back)

    @app.get("/datasets/<int:dataset_id>/export")
    def export(dataset_id):
        _, fields = get_dataset(dataset_id)
        filters, _ = query_state(fields)
        where, params = database.where_clause(dataset_id, fields, filters, request.args.get("q", "").strip())
        order, order_params = database.order_clause(fields, request.args.get("sort", ""), request.args.get("direction", "asc"))
        path = app.config["DATABASE"]
        export_key = comment_export_key(fields)

        def generate():
            connection = database.connect(path)
            try:
                for row in connection.execute(f"SELECT r.payload, c.comment FROM records r LEFT JOIN record_comments c ON c.record_id=r.id WHERE {where} ORDER BY {order}", params + order_params):
                    yield annotated_payload(row, export_key) + "\n"
            finally:
                connection.close()

        return Response(generate(), mimetype="application/x-ndjson", headers={"Content-Disposition": f'attachment; filename="dataset-{dataset_id}.jsonl"'})

    @app.get("/datasets/<int:dataset_id>/records/<int:record_id>/download")
    def download_record(dataset_id, record_id):
        _, fields = get_dataset(dataset_id)
        row = db().execute("SELECT r.payload, c.comment FROM records r LEFT JOIN record_comments c ON c.record_id=r.id WHERE r.id=? AND r.dataset_id=?", (record_id, dataset_id)).fetchone()
        if row is None:
            abort(404)
        return Response(annotated_payload(row, comment_export_key(fields)) + "\n", mimetype="application/json", headers={"Content-Disposition": f'attachment; filename="record-{record_id}.json"'})

    @app.errorhandler(database.DataError)
    def data_error(error):
        return render_template("error.html", message=str(error), active_id=None), 400

    @app.errorhandler(413)
    def too_large(error):
        return render_template("error.html", message="Browser uploads are limited to 128 MiB. Use the command-line importer for larger files.", active_id=None), 413

    @app.errorhandler(400)
    @app.errorhandler(404)
    def http_error(error):
        return render_template("error.html", message=error.description, active_id=None), error.code

    @app.errorhandler(sqlite3.OperationalError)
    def database_error(error):
        app.logger.exception("SQLite operation failed")
        return render_template("error.html", message="The database is temporarily unavailable. Check available disk space and retry after any running import finishes.", active_id=None), 503

    return app


def main():
    parser = argparse.ArgumentParser(description="Explore engine JSONL in SQLite.")
    parser.add_argument("--db", default=os.environ.get("EXPLORER_DB", str(ROOT / "data" / "explorer.sqlite3")))
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Start the web application")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    ingest = commands.add_parser("import", help="Import JSONL without a browser upload limit")
    ingest.add_argument("file", type=Path)
    ingest.add_argument("--name")
    commands.add_parser("demo", help="Import the included sample dataset")
    args = parser.parse_args()
    database.initialize(args.db)
    if args.command == "serve":
        from waitress import serve as run
        print(f"Engine Explorer is running at http://{args.host}:{args.port}", flush=True)
        run(create_app({"DATABASE": args.db}), host=args.host, port=args.port, threads=4)
    else:
        path = args.file if args.command == "import" else ROOT / "examples" / "engine-output.jsonl"
        name = (args.name or path.stem) if args.command == "import" else "Materials screening · sample"
        connection = database.connect(args.db)
        try:
            with path.open("rb") as stream:
                dataset_id = database.import_jsonl(connection, stream, name, path.name)
            print(f"Imported dataset {dataset_id}: {name}")
        except (OSError, database.DataError) as error:
            parser.exit(1, f"Import failed: {error}\n")
        finally:
            connection.close()


if __name__ == "__main__":
    main()
