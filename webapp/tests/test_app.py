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

    def test_invalid_record_limit_is_actionable(self):
        for value in ["0", "-1", "bad", "1.5"]:
            with patch.dict(os.environ, {"EXPLORER_MAX_RECORD_MIB": value}):
                response = self.ingest([{"a": 1}])
            self.assertEqual(response.status_code, 400)
            self.assertIn(b"must be a positive whole number", response.data)


if __name__ == "__main__":
    unittest.main()
