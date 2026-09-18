from flask import abort, flash, redirect, render_template, request, Response, url_for
import database
import report_store


def register_reports(app, db, get_dataset, root):
    @app.get('/datasets/<int:dataset_id>/reports')
    def reports(dataset_id):
        dataset, fields = get_dataset(dataset_id)
        choices = report_store.scalar_fields(fields)
        preferred_record = next((field for field in ('/record_id','/genome/accession','/genome/currentAccession','/genome.assembly_accession') if field in choices), '')
        preferred_run = next((field for field in ('/run_id','/run/id') if field in choices), '')
        return render_template('reports.html', dataset=dataset, active_id=dataset_id,
                               reports=report_store.attachments(db(), dataset_id), match_fields=choices,
                               preferred_record=preferred_record, preferred_run=preferred_run)

    @app.post('/datasets/<int:dataset_id>/reports')
    def attach_report(dataset_id):
        _, fields = get_dataset(dataset_id)
        uploaded = request.files.get('file')
        if not uploaded or not uploaded.filename:
            raise database.DataError('Choose a JSONL report to attach.')
        source = uploaded.filename.replace('\\', '/').rsplit('/', 1)[-1][:200]
        report_id = report_store.import_report(db(), dataset_id, fields, uploaded.stream,
            request.form.get('name',''), source, request.form.get('record_field',''), request.form.get('run_field',''))
        report = report_store.get_report(db(), dataset_id, report_id)
        flash(f'Attached report: {report["matched_records"]:,} dataset rows matched; {report["matched_entries"]:,} of {report["entry_count"]:,} report entries matched. Unmatched entries are retained for review.', 'success')
        return redirect(url_for('explore', dataset_id=dataset_id, report_id=report_id, report_scope='matched'), code=303)

    @app.post('/datasets/<int:dataset_id>/reports/<int:report_id>/delete')
    def delete_report(dataset_id, report_id):
        report = report_store.get_report(db(), dataset_id, report_id)
        if request.form.get('confirm') != 'delete':
            abort(400, 'Confirm report deletion before continuing.')
        with db():
            db().execute('DELETE FROM result_reports WHERE id=? AND dataset_id=?', (report_id,dataset_id))
        flash(f'Deleted report “{report["name"]}”. Dataset records and comments are unchanged.', 'success')
        return redirect(url_for('reports', dataset_id=dataset_id), code=303)

    @app.get('/datasets/<int:dataset_id>/reports/<int:report_id>/download')
    def download_report(dataset_id, report_id):
        report_store.get_report(db(), dataset_id, report_id)
        where = 'e.report_id=?'
        unmatched = request.args.get('unmatched') == '1'
        if unmatched:
            where += ' AND NOT EXISTS (SELECT 1 FROM report_matches m WHERE m.entry_id=e.id)'
        path = app.config['DATABASE']
        def generate():
            connection = database.connect(path)
            try:
                for row in connection.execute(f'SELECT e.payload FROM report_entries e WHERE {where} ORDER BY e.line_number', (report_id,)):
                    yield row['payload'] + '\n'
            finally:
                connection.close()
        suffix = '-unmatched' if unmatched else ''
        return Response(generate(), mimetype='application/x-ndjson', headers={'Content-Disposition':f'attachment; filename="report-{report_id}{suffix}.jsonl"'})

    @app.post('/demo/reports')
    def report_demo():
        with (root / 'examples/report-dataset.jsonl').open('rb') as stream:
            dataset_id = database.import_jsonl(db(), stream, 'Result reports · demo', 'report-dataset.jsonl')
        _, fields = get_dataset(dataset_id)
        with (root / 'examples/result-report.jsonl').open('rb') as stream:
            report_id = report_store.import_report(db(), dataset_id, fields, stream, 'Example ontology evaluation · demo', 'result-report.jsonl', '/record_id', '/run_id')
        flash('Demo ready: four dataset rows, two matched rows, and six ontology results. The original NCBI dataset is unchanged.', 'success')
        return redirect(url_for('explore', dataset_id=dataset_id, report_id=report_id, report_scope='matched'), code=303)
