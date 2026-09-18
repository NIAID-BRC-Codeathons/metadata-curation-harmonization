import io
import json
from pathlib import Path
from urllib.parse import urlencode

from flask import abort, flash, redirect, render_template, request, url_for, Response

import database
from relationships import Graph

MAX_GRAPH_BYTES = 8 * 1024 * 1024
REPO_INPUTS = ("data/raw/records.jsonl", "data/out/proposals.jsonl")


def repository_rows(root):
    collections = []
    for relative in REPO_INPUTS:
        path = Path(root) / relative
        if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
            raise database.DataError(f"Repository graph input is missing or exceeds 16 MiB: {relative}")
        rows = {}
        with path.open(encoding="utf-8") as stream:
            for line_no, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    identifier = row.get("record_id") if isinstance(row, dict) else None
                    if not isinstance(identifier, str) or not identifier or identifier in rows:
                        raise ValueError("Each row needs a unique string record_id")
                    rows[identifier] = row
                    if len(rows) > 10000:
                        raise ValueError("Use at most 10,000 records in this convenience import")
                except ValueError as error:
                    raise database.DataError(f"{relative}, line {line_no}: {error}") from error
        collections.append(rows)
    raw, proposals = collections
    if not raw:
        raise database.DataError("Repository input contains no source records.")
    matched = sum(identifier in proposals for identifier in raw)
    rows = [{"source_record": row, "proposal": proposals.get(identifier), "source_files": list(REPO_INPUTS)} for identifier, row in raw.items()]
    return rows, matched, len(set(proposals) - set(raw))


def register_graph(app, db, get_dataset, query_state):
    @app.context_processor
    def graph_context():
        root = Path(app.config["REPOSITORY_ROOT"])
        return {"repository_graph_available": all((root / name).is_file() for name in REPO_INPUTS)}

    @app.post("/import/repository-graph")
    def import_repository_graph():
        rows, matched, unmatched = repository_rows(app.config["REPOSITORY_ROOT"])
        content = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode()
        dataset_id = database.import_jsonl(db(), io.BytesIO(content), "Repository curation results", " + ".join(REPO_INPUTS))
        message = f"Imported {len(rows):,} source records with {matched:,} matching curation proposals. Proposed disease links are unreviewed."
        if unmatched:
            message += f" {unmatched:,} unmatched proposals were not attached."
        flash(message, "success")
        return redirect(url_for("relationship_graph", dataset_id=dataset_id), code=303)

    @app.get("/datasets/<int:dataset_id>/graph")
    def relationship_graph(dataset_id):
        dataset, fields = get_dataset(dataset_id)
        filters, _ = query_state(fields)
        search = request.args.get("q", "").strip()
        where, params = database.where_clause(dataset_id, fields, filters, search)
        order, order_params = database.order_clause(fields, request.args.get("sort", ""), request.args.get("direction", "asc"))
        try:
            size = int(request.args.get("graph_size", "25"))
            page = max(1, int(request.args.get("graph_page", "1")))
            focus = int(request.args["record_id"]) if request.args.get("record_id") else None
        except ValueError:
            raise database.DataError("Graph page, record ID, and record limit must be whole numbers.") from None
        if size not in {10, 25, 50, 100}:
            raise database.DataError("Choose 10, 25, 50, or 100 records per graph.")
        if focus is not None:
            if not db().execute("SELECT 1 FROM records WHERE dataset_id=? AND id=?", (dataset_id, focus)).fetchone():
                abort(404, "Record not found in this dataset.")
            where += " AND r.id=?"
            params.append(focus)
        count = db().execute(f"SELECT count(*) FROM records r WHERE {where}", params).fetchone()[0]
        pages = max(1, (count + size - 1) // size)
        page = min(page, pages)
        # Fetch lengths first so an unusually large JSON record cannot exhaust page memory.
        candidates = db().execute(f"SELECT r.id, r.line_number, length(CAST(r.payload AS BLOB)) AS bytes FROM records r WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?", params + order_params + [size, (page - 1) * size]).fetchall()
        graph = Graph()
        used_bytes = skipped = 0
        included = []
        for row in candidates:
            if used_bytes + row["bytes"] > MAX_GRAPH_BYTES:
                skipped += 1
                continue
            payload = db().execute("SELECT payload FROM records WHERE id=?", (row["id"],)).fetchone()[0]
            used_bytes += row["bytes"]
            graph.add_record(row, json.loads(payload))
            included.append(row["id"])
        result = graph.result()
        result["scope"] = {"dataset_id": dataset_id, "dataset_name": dataset["name"], "matching_records": count,
                           "records_examined": len(included), "records_skipped_for_size": skipped,
                           "page": page, "pages": pages, "record_limit": size,
                           "node_limit": graph.max_nodes, "edge_limit": graph.max_edges}
        result["record_urls"] = {str(rid): url_for("detail", dataset_id=dataset_id, record_id=rid) for rid in included}
        if request.args.get("format") == "json":
            return Response(json.dumps(result, ensure_ascii=False), mimetype="application/json", headers={"Content-Disposition": f'attachment; filename="dataset-{dataset_id}-graph.json"'})

        def graph_url(**changes):
            args = request.args.to_dict(flat=False)
            args.pop("format", None)
            for key, value in changes.items():
                args[key] = [str(value)]
            return url_for("relationship_graph", dataset_id=dataset_id) + "?" + urlencode(args, doseq=True)

        table_args = {key: values for key, values in request.args.lists() if key not in {"graph_page", "graph_size", "record_id", "format"}}
        table_url = url_for("explore", dataset_id=dataset_id) + "?" + urlencode(table_args, doseq=True)
        preserved = [(key, value) for key, values in request.args.lists() if key not in {"graph_page", "graph_size", "q", "format"} for value in values]
        return render_template("graph.html", dataset=dataset, active_id=dataset_id, graph=result,
                               table_url=table_url, graph_url=graph_url, preserved=preserved,
                               search=search, filters=filters, focus=focus)
