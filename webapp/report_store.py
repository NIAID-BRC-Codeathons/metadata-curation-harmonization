"""Dataset-scoped result reports with exact record/run matching."""
import json
import hashlib
from datetime import datetime, timezone

from flask import abort
import database

MAX_REPORT_LINE = 1024 * 1024
MAX_REPORT_ROWS = 100000


def fingerprint(obj):
    # Key order and JSON whitespace do not make a new result; every value does.
    canonical = json.dumps(obj, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    return hashlib.sha256(canonical.encode()).hexdigest()


DISTINCT_ENTRY = "NOT EXISTS (SELECT 1 FROM report_entries duplicate WHERE duplicate.report_id=e.report_id AND duplicate.content_hash=e.content_hash AND duplicate.id<e.id)"



def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS result_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
            name TEXT NOT NULL, source TEXT NOT NULL, imported_at TEXT NOT NULL,
            record_field TEXT NOT NULL, run_field TEXT NOT NULL,
            entry_count INTEGER NOT NULL DEFAULT 0,
            matched_entries INTEGER NOT NULL DEFAULT 0,
            matched_records INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS reports_dataset ON result_reports(dataset_id,id);
        CREATE TABLE IF NOT EXISTS report_entries (
            id INTEGER PRIMARY KEY, report_id INTEGER NOT NULL REFERENCES result_reports(id) ON DELETE CASCADE,
            line_number INTEGER NOT NULL, record_key TEXT NOT NULL, run_key TEXT NOT NULL, payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS entries_report_run ON report_entries(report_id,run_key,id);
        CREATE TABLE IF NOT EXISTS report_matches (
            entry_id INTEGER NOT NULL REFERENCES report_entries(id) ON DELETE CASCADE,
            record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
            PRIMARY KEY(entry_id,record_id)
        );
        CREATE INDEX IF NOT EXISTS matches_record ON report_matches(record_id,entry_id);
    """)

    # Upgrade existing attachments in place; retain every uploaded source line.
    columns = {row['name'] for row in db.execute('PRAGMA table_info(report_entries)')}
    if 'content_hash' not in columns:
        db.execute('ALTER TABLE report_entries ADD COLUMN content_hash TEXT')
    for row in db.execute('SELECT id,payload FROM report_entries WHERE content_hash IS NULL'):
        db.execute('UPDATE report_entries SET content_hash=? WHERE id=?', (fingerprint(json.loads(row['payload'])),row['id']))
    db.execute('CREATE INDEX IF NOT EXISTS entries_content ON report_entries(report_id,content_hash,id)')


def scalar_fields(fields):
    return {key: kinds for key, kinds in fields.items() if 'text' in kinds}


def import_report(db, dataset_id, fields, stream, name, source, record_field, run_field):
    allowed = scalar_fields(fields)
    if record_field not in allowed or run_field not in allowed or record_field == run_field:
        raise database.DataError('Choose two different text fields for record_id and run_id matching.')
    if not db.execute('SELECT 1 FROM datasets WHERE id=?', (dataset_id,)).fetchone():
        raise database.DataError('Dataset no longer exists.')
    count = line_number = 0
    with db:
        report_id = db.execute('INSERT INTO result_reports(dataset_id,name,source,imported_at,record_field,run_field) VALUES (?,?,?,?,?,?)',
                              (dataset_id, name.strip()[:200] or source, source, datetime.now(timezone.utc).isoformat(), record_field, run_field)).lastrowid
        while True:
            raw = stream.readline(MAX_REPORT_LINE + 1)
            if not raw:
                break
            line_number += 1
            try:
                if len(raw) > MAX_REPORT_LINE:
                    raise database.DataError('Report rows must be at most 1 MiB.')
                if isinstance(raw, bytes):
                    raw = raw.decode('utf-8-sig' if line_number == 1 else 'utf-8')
                if not raw.strip():
                    continue
                obj = json.loads(raw, parse_constant=database.reject_constant, object_pairs_hook=database.unique_object)
                if not isinstance(obj, dict):
                    raise database.DataError('Each report line must be a JSON object.')
                for key in ('record_id', 'run_id'):
                    if not isinstance(obj.get(key), str) or not obj[key].strip():
                        raise database.DataError(f'{key} must be a nonempty string.')
                # Reuse the dataset validation for nested values and depth.
                list(database.flatten(obj))
                count += 1
                if count > MAX_REPORT_ROWS:
                    raise database.DataError('A report may contain at most 100,000 entries.')
                db.execute('INSERT INTO report_entries(report_id,line_number,record_key,run_key,payload,content_hash) VALUES (?,?,?,?,?,?)',
                           (report_id, line_number, obj['record_id'], obj['run_id'], json.dumps(obj, ensure_ascii=False, allow_nan=False), fingerprint(obj)))
            except (ValueError, UnicodeError, RecursionError) as error:
                raise database.DataError(f'Report line {line_number}: {error}') from error
        if not count:
            raise database.DataError('The report contains no JSON objects.')
        # Indexed, case-sensitive equality on BOTH keys, restricted to the target dataset.
        db.execute('''INSERT INTO report_matches(entry_id,record_id)
                      SELECT e.id,r.id FROM report_entries e
                      JOIN record_values identity ON identity.field=? AND identity.value=e.record_key AND identity.kind='text'
                      JOIN records r ON r.id=identity.record_id AND r.dataset_id=?
                      JOIN record_values run ON run.record_id=r.id AND run.field=? AND run.value=e.run_key AND run.kind='text'
                      WHERE e.report_id=?''', (record_field, dataset_id, run_field, report_id))
        matched_entries, matched_records = db.execute('''SELECT count(DISTINCT m.entry_id),count(DISTINCT m.record_id)
            FROM report_matches m JOIN report_entries e ON e.id=m.entry_id WHERE e.report_id=?''', (report_id,)).fetchone()
        db.execute('UPDATE result_reports SET entry_count=?,matched_entries=?,matched_records=? WHERE id=?',
                   (count, matched_entries, matched_records, report_id))
    return report_id


def attachments(db, dataset_id):
    return db.execute('SELECT * FROM result_reports WHERE dataset_id=? ORDER BY id DESC', (dataset_id,)).fetchall()


def get_report(db, dataset_id, report_id):
    row = db.execute('SELECT * FROM result_reports WHERE dataset_id=? AND id=?', (dataset_id, report_id)).fetchone()
    if row is None:
        abort(404, 'Report not found in this dataset.')
    return row


def selection(db, dataset_id, args):
    raw = args.get('report_id', '')
    try:
        report_id = int(raw) if raw else None
    except ValueError:
        raise database.DataError('Choose a valid report.') from None
    if report_id is not None:
        get_report(db, dataset_id, report_id)
    scope = args.get('report_scope', 'matched' if report_id is not None else 'all')
    if scope not in {'all', 'matched', 'matched_records', 'unmatched'}:
        raise database.DataError('Choose all rows, matched report results, matched dataset rows, or unmatched rows.')
    return {'id': report_id, 'scope': scope, 'run': args.get('report_run', '')}


def apply_filter(db, dataset_id, args, where, params):
    state = selection(db, dataset_id, args)
    if state['scope'] != 'all':
        sub = 'SELECT 1 FROM report_matches m JOIN report_entries e ON e.id=m.entry_id WHERE m.record_id=r.id'
        if state['id'] is not None:
            sub += ' AND e.report_id=?'
            params.append(state['id'])
        if state['run']:
            sub += ' AND e.run_key=?'
            params.append(state['run'])
        where += (' AND NOT EXISTS (' if state['scope'] == 'unmatched' else ' AND EXISTS (') + sub + ')'
    return where, params


def record_results(db, dataset_id, record_id, args):
    state = selection(db, dataset_id, args)
    where = 'm.record_id=? AND a.dataset_id=?'
    params = [record_id, dataset_id]
    if state['id'] is not None:
        where += ' AND a.id=?'
        params.append(state['id'])
    if state['run']:
        where += ' AND e.run_key=?'
        params.append(state['run'])
    source = 'report_matches m JOIN report_entries e ON e.id=m.entry_id JOIN result_reports a ON a.id=e.report_id'
    count = db.execute(f'SELECT count(*) FROM {source} WHERE {where}', params).fetchone()[0]
    try:
        page = max(1, int(args.get('result_page', '1')))
    except ValueError:
        raise database.DataError('Result page must be a whole number.') from None
    pages = max(1, (count + 24) // 25)
    page = min(page, pages)
    rows = db.execute(f'''SELECT e.*,a.name,a.source,a.record_field,a.run_field
        FROM {source} WHERE {where} ORDER BY a.id DESC,e.run_key,e.line_number LIMIT 25 OFFSET ?''', params + [(page-1)*25]).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        obj = json.loads(row['payload'])
        item['ontology'] = obj.get('ontology')
        item['values'] = list(database.flatten(obj))
        items.append(item)
    total = db.execute('SELECT count(*) FROM report_matches WHERE record_id=?', (record_id,)).fetchone()[0]
    return {'items': items, 'count': count, 'total': total, 'page': page, 'pages': pages, 'selection': state}


def matched_query(state, where, params):
    """One row per distinct report entry and matching source record."""
    source = ('records r JOIN report_matches m ON m.record_id=r.id '
              'JOIN report_entries e ON e.id=m.entry_id '
              'JOIN result_reports a ON a.id=e.report_id '
              'LEFT JOIN record_comments c ON c.record_id=r.id')
    params = list(params)
    if state['id'] is not None:
        where += ' AND e.report_id=?'
        params.append(state['id'])
    if state['run']:
        where += ' AND e.run_key=?'
        params.append(state['run'])
    where += ' AND ' + DISTINCT_ENTRY
    return source, where, params


MATCHED_COLUMNS = ('r.*,c.comment,e.id AS report_entry_id,e.report_id,e.line_number AS report_line,'
                   'e.payload AS report_payload,e.run_key,a.name AS report_name')


def matched_export(row, dataset_record):
    return {'dataset_record_id':row['id'], 'dataset_record':dataset_record,
            'report_id':row['report_id'], 'report_line':row['report_line'],
            'result_report':json.loads(row['report_payload'])}
