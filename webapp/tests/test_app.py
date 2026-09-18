import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import patch

from app import create_app
import database


class ExplorerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "test.sqlite3")
        self.app = create_app({"TESTING": True, "DATABASE": self.path, "SECRET_KEY": "test"})
        self.client = self.app.test_client()
        self.client.get("/")
        with self.client.session_transaction() as session:
            self.csrf = session["csrf"]

    def tearDown(self):
        self.temp.cleanup()

    def ingest(self, objects=None, raw=None):
        if raw is None:
            raw = "\n".join(json.dumps(obj) for obj in objects).encode()
        return self.client.post("/import", data={"csrf": self.csrf, "name": "Test dataset", "file": (io.BytesIO(raw), "test.jsonl")}, content_type="multipart/form-data")

    def seed(self):
        response = self.ingest([{"name": "alpha", "score": 2, "nested": {"flag": True}, "nullable": None}, {"name": "beta", "score": 10}, {"name": "gamma", "score": 100}])
        self.assertEqual(response.status_code, 302)
        return response.headers["Location"]

    def test_empty_and_sample(self):
        self.assertIn(b"Import your first dataset", self.client.get("/").data)
        response = self.client.post("/demo", data={"csrf": self.csrf}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"MAT-0001", response.data)
        self.assertIn(b"96", response.data)

    def test_import_detail_original_and_nested_fields(self):
        url = self.seed()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"nested", response.data)
        detail = self.client.get(url + "/records/1?q=alpha")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Source line 1", detail.data)
        self.assertIn(b"?q=alpha", detail.data)
        record = self.client.get(url + "/records/1/download").json
        self.assertEqual(record["nested"], {"flag": True})
        self.assertIsNone(record["nullable"])

    def test_atomic_rollback(self):
        for raw in [b'{"a":1}\ninvalid', b'{"a":1}\n[]', b'{"a":1}\n{"n":NaN}', b'{"a":1}\n{"a":1,"a":2}']:
            response = self.ingest(raw=raw)
            self.assertEqual(response.status_code, 400)
            self.assertIn(b"Line 2", response.data)
        connection = database.connect(self.path)
        try:
            for table in ["datasets", "records", "record_values"]:
                self.assertEqual(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)
        finally:
            connection.close()

    def test_empty_file_and_encoding(self):
        for raw in [b"\n \n", b'{"bad":"\xff"}']:
            self.assertEqual(self.ingest(raw=raw).status_code, 400)
        response = self.ingest(raw=b'\xef\xbb\xbf{"a":1}\n\n{"a":2}\n')
        self.assertEqual(response.status_code, 302)
        record = self.client.get(response.location + "/records/2")
        self.assertIn(b"Source line 3", record.data)

    def exported(self, url, args):
        response = self.client.get(url + "/export?" + urlencode(args, doseq=True))
        self.assertEqual(response.status_code, 200)
        return [json.loads(line) for line in response.data.splitlines()]

    def test_numeric_filter_and_sort(self):
        url = self.seed()
        rows = self.exported(url, {"f": "/score", "op": "gte", "v": "10", "sort": "/score", "direction": "desc"})
        self.assertEqual([row["score"] for row in rows], [100, 10])
        rows = self.exported(url, {"f": "/score", "op": "eq", "v": "2.0"})
        self.assertEqual(len(rows), 1)

    def test_combined_filters_null_missing_and_present(self):
        url = self.seed()
        rows = self.exported(url, {"f": ["/score", "/name"], "op": ["gt", "contains"], "v": ["2", "BETA"]})
        self.assertEqual([r["name"] for r in rows], ["beta"])
        for op, count in [("null", 1), ("missing", 2), ("exists", 1)]:
            self.assertEqual(len(self.exported(url, {"f": "/nullable", "op": op, "v": ""})), count)

    def test_search_literal_wildcards_and_sql_injection(self):
        url = self.ingest([{"a": "100%_ready"}, {"a": "ordinary"}, {"a": "O'Reilly"}]).location
        self.assertEqual(len(self.exported(url, {"q": "%_"})), 1)
        self.assertEqual(len(self.exported(url, {"q": "' OR 1=1 --"})), 0)
        self.assertEqual(len(self.exported(url, {"f": "/a", "op": "eq", "v": "O'Reilly"})), 1)
        self.assertEqual(self.client.get(url + "?sort=bad%27").status_code, 400)

    def test_unusual_keys_remain_distinct(self):
        obj = {'a.b': 1, 'a': {'b': 2}, 'a/b': 3, '~': 4, '"; DROP TABLE records;--': 5, '': 6}
        url = self.ingest([obj]).location
        for key, value in [("/a.b", "1"), ("/a/b", "2"), ("/a~1b", "3"), ("/~0", "4"), ("/", "6")]:
            self.assertEqual(self.exported(url, {"f": key, "op": "eq", "v": value}), [obj])

    def test_pagination_columns_and_export_all_matches(self):
        url = self.ingest([{"item": i, "secret": "hidden value"} for i in range(61)]).location
        response = self.client.get(url + "?col=/item&page=3&size=25")
        self.assertIn(b"51", response.data)
        self.assertNotIn(b"hidden value", response.data)
        self.assertEqual(len(self.exported(url, {"page": 3, "size": 25})), 61)
        self.assertEqual(self.client.get(url + "?page=999999").status_code, 200)
        self.assertEqual(self.client.get(url + "?page=bad").status_code, 400)

    def test_invalid_filters(self):
        url = self.seed()
        for query in ["f=/score", "f=/score&op=gt&v=nan", "f=/bad&op=eq&v=1", "f=/score&op=bad&v=1"]:
            self.assertEqual(self.client.get(url + "?" + query).status_code, 400)

    def test_empty_objects(self):
        url = self.ingest([{}]).location
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertIn(b"empty JSON object", self.client.get(url + "/records/1").data)

    def test_csrf_and_html_escaping(self):
        self.assertEqual(self.client.post("/demo").status_code, 400)
        url = self.ingest([{"html": '<script>alert("x")</script>'}]).location
        response = self.client.get(url)
        self.assertNotIn(b'<script>alert', response.data)
        self.assertIn(b'&lt;script&gt;', response.data)
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

    def test_dataset_isolation_and_missing_record(self):
        first = self.seed()
        second = self.ingest([{"other": 1}]).location
        self.assertEqual(self.client.get(second + "/records/1").status_code, 404)
        self.assertEqual(self.client.get(first + "/records/999/download").status_code, 404)
        self.assertEqual(len(self.exported(second, {})), 1)

    def test_field_limit_and_nonfinite_nested_array_rollback(self):
        self.assertEqual(self.ingest([{str(i): i for i in range(501)}]).status_code, 400)
        self.assertEqual(self.ingest(raw=b'{"a": [1e400]}').status_code, 400)

    def test_records_over_old_eight_mib_limit_and_configurable_limit(self):
        # Real NCBI records can exceed the former 8 MiB cap.
        obj = {"sra-experiment": "x" * (9 * 1024 * 1024)}
        response = self.ingest([obj])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(json.loads(self.client.get(response.location + "/export").data), obj)
        with patch.dict(os.environ, {"EXPLORER_MAX_RECORD_MIB": "1"}):
            response = self.ingest([obj])
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Line 1: Record exceeds the 1 MiB line limit", response.data)
        self.assertIn(b"EXPLORER_MAX_RECORD_MIB", response.data)
        connection = database.connect(self.path)
        try:
            self.assertEqual(connection.execute("SELECT count(*) FROM datasets").fetchone()[0], 1)
        finally:
            connection.close()

    def test_comment_save_export_clear_and_persistence(self):
        url = self.seed()
        endpoint = url + "/records/2/comment"
        comment = 'Review this row\nUnicode: café 🧬 <script>alert(1)</script>'
        response = self.client.post(endpoint, data={"csrf": self.csrf, "comment": comment}, headers={"Accept": "application/json"})
        self.assertEqual(response.json, {"saved": True})
        # Reinitializing an existing database preserves records and annotations.
        database.initialize(self.path)
        page = self.client.get(url).data
        self.assertIn(b'&lt;script&gt;', page)
        self.assertNotIn(b'<script>alert(1)</script>', page)
        self.assertIn(b'caf', self.client.get(url + "/records/2").data)
        rows = self.exported(url, {"sort": "/score", "direction": "desc", "col": "/name", "page": 99})
        self.assertEqual([row["score"] for row in rows], [100, 10, 2])
        self.assertEqual(rows[1]["comments"], comment)
        self.assertNotIn("comments", rows[0])
        self.assertEqual(self.exported(url, {"q": "beta"})[0]["comments"], comment)
        self.assertEqual(self.client.get(url + "/records/2/download").json["comments"], comment)
        response = self.client.post(endpoint + "?q=beta", data={"csrf": self.csrf, "comment": ""})
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.location.endswith("?q=beta"))
        self.assertNotIn("comments", self.exported(url, {"q": "beta"})[0])

    def test_comments_do_not_overwrite_original_fields(self):
        original = {"comments": {"nested": "original"}, "comments_2": "also original"}
        url = self.ingest([original, {"other": 1}]).location
        for record_id in (1, 2):
            self.client.post(url + f"/records/{record_id}/comment", data={"csrf": self.csrf, "comment": "annotation"})
        rows = self.exported(url, {})
        self.assertEqual(rows[0], {**original, "comments_3": "annotation"})
        self.assertEqual(rows[1], {"other": 1, "comments_3": "annotation"})
        self.assertEqual(self.client.get(url + "/records/2/download").json, rows[1])

    def test_existing_database_gains_comment_storage_without_reimport(self):
        url = self.seed()
        connection = database.connect(self.path)
        try:
            connection.execute("DROP TABLE record_comments")
            connection.commit()
        finally:
            connection.close()
        database.initialize(self.path)
        response = self.client.post(url + "/records/1/comment", data={"csrf": self.csrf, "comment": "Added after upgrade"})
        self.assertEqual(response.status_code, 303)
        rows = self.exported(url, {})
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["comments"], "Added after upgrade")

    def test_failed_deletion_rolls_back_records_values_and_comments(self):
        url = self.seed()
        self.client.post(url + "/records/1/comment", data={"csrf": self.csrf, "comment": "Keep this"})
        connection = database.connect(self.path)
        try:
            connection.execute("CREATE TRIGGER prevent_delete BEFORE DELETE ON datasets BEGIN SELECT RAISE(ABORT, 'test failure'); END")
            connection.commit()
        finally:
            connection.close()
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.client.post(url + "/delete", data={"csrf": self.csrf, "confirm": "delete"})
        self.assertEqual(len(self.exported(url, {})), 3)
        self.assertEqual(self.exported(url, {"q": "alpha"})[0]["comments"], "Keep this")

    def test_comment_validation_and_dataset_isolation(self):
        first = self.seed()
        second = self.ingest([{"other": 1}]).location
        endpoint = first + "/records/1/comment"
        self.assertEqual(self.client.get(endpoint).status_code, 405)
        self.assertEqual(self.client.post(endpoint, data={"comment": "no csrf"}).status_code, 400)
        self.assertEqual(self.client.post(endpoint, data={"csrf": self.csrf, "comment": "x" * 10001}).status_code, 400)
        self.assertEqual(self.client.post(second + "/records/1/comment", data={"csrf": self.csrf, "comment": "wrong dataset"}).status_code, 404)
        self.assertNotIn("comments", self.exported(first, {})[0])

    def test_column_selection_can_hide_all_or_show_only_comments(self):
        url = self.seed()
        self.assertIn(b'<th>Comments</th>', self.client.get(url).data)
        page = self.client.get(url + "?columns_set=1").data
        self.assertNotIn(b'<textarea', page)
        self.assertNotIn(b'title="Sort by', page)
        page = self.client.get(url + "?columns_set=1&col=__comments").data
        self.assertIn(b'<textarea', page)
        self.assertNotIn(b'title="Sort by', page)
        page = self.client.get(url + "?col=__comments&col=/name").data
        self.assertLess(page.index(b'title="Sort by name"'), page.index(b'<th>Comments</th>'))
        self.assertIn(b'id="deselect-all-columns"', page)

    def test_delete_requires_confirmation_and_removes_only_selected_dataset(self):
        first = self.seed()
        second = self.ingest([{"other": 1}]).location
        self.client.post(first + "/records/1/comment", data={"csrf": self.csrf, "comment": "remove me"})
        self.client.post(second + "/records/4/comment", data={"csrf": self.csrf, "comment": "keep me"})
        endpoint = first + "/delete"
        self.assertEqual(self.client.get(endpoint).status_code, 405)
        self.assertEqual(self.client.post(endpoint, data={"confirm": "delete"}).status_code, 400)
        self.assertEqual(self.client.post(endpoint, data={"csrf": self.csrf}).status_code, 400)
        self.assertEqual(len(self.exported(first, {})), 3)
        self.assertEqual(self.client.post(endpoint, data={"csrf": self.csrf, "confirm": "delete"}).status_code, 303)
        self.assertEqual(self.client.get(first).status_code, 404)
        self.assertEqual(self.exported(second, {}), [{"other": 1, "comments": "keep me"}])
        connection = database.connect(self.path)
        try:
            self.assertEqual(connection.execute("SELECT count(*) FROM record_values").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM record_comments").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            connection.close()
        self.assertEqual(self.client.post(endpoint, data={"csrf": self.csrf, "confirm": "delete"}).status_code, 404)
        response = self.client.post(second + "/delete", data={"csrf": self.csrf, "confirm": "delete"}, follow_redirects=True)
        self.assertIn(b"Import your first dataset", response.data)

    def test_invalid_record_limit_is_actionable(self):
        for value in ["0", "-1", "bad", "1.5"]:
            with patch.dict(os.environ, {"EXPLORER_MAX_RECORD_MIB": value}):
                response = self.ingest([{"a": 1}])
            self.assertEqual(response.status_code, 400)
            self.assertIn(b"must be a positive whole number", response.data)


if __name__ == "__main__":
    unittest.main()
