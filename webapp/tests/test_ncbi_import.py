import gzip
import io
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from app import create_app
import database
from ncbi_import import NCBI_URL, NCBI_FILENAME


class ArchiveResponse(io.BytesIO):
    def __init__(self, data, advertised_length=None):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data) if advertised_length is None else advertised_length)}
        self.headers["Last-Modified"] = "Thu, 17 Sep 2026 18:11:25 GMT"
        self.status = 200
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


class NcbiImportTest(unittest.TestCase):
    def setUp(self):
        self.stderr = io.StringIO()
        self.stderr_patch = patch("sys.stderr", self.stderr)
        self.stderr_patch.start()
        self.sleep_patch = patch("ncbi_download.sleep")
        self.sleep_patch.start()
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "test.sqlite3")
        self.app = create_app({"TESTING": True, "DATABASE": self.path, "SECRET_KEY": "test"})
        self.client = self.app.test_client()
        self.client.get("/")
        with self.client.session_transaction() as session:
            self.csrf = session["csrf"]
        self.jobs = self.app.extensions["ncbi_imports"]

    def tearDown(self):
        self.sleep_patch.stop()
        self.stderr_patch.stop()
        self.temp.cleanup()

    def archive_opener(self, archive):
        payload = archive.getvalue()
        def open_response(request, timeout):
            if request.get_method() == "HEAD":
                result = ArchiveResponse(b"")
                result.headers = dict(archive.headers)
                return result
            if not archive.closed:
                return archive
            result = ArchiveResponse(payload)
            result.headers = dict(archive.headers)
            return result
        return open_response

    def start(self):
        response = self.client.post("/import/ncbi", data={"csrf": self.csrf})
        self.assertEqual(response.status_code, 303)
        return response.location

    def finished(self, url):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = self.jobs.snapshot(url.rsplit("/", 1)[-1])
            with self.jobs.lock:
                active = self.jobs.active_id
            if job["state"] != "running" and active is None:
                return job
            time.sleep(0.01)
        self.fail("Background import did not finish")

    def assert_no_partial_dataset(self):
        connection = database.connect(self.path)
        try:
            for table in ["datasets", "records", "record_values"]:
                self.assertEqual(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)
        finally:
            connection.close()

    def test_button_requires_post_and_csrf(self):
        self.assertIn(b"Import Brad's data", self.client.get("/").data)
        with patch("ncbi_import.urlopen") as opened:
            self.assertEqual(self.client.get("/import/ncbi").status_code, 405)
            self.assertEqual(self.client.post("/import/ncbi").status_code, 400)
            opened.assert_not_called()

    def test_cached_import_appears_in_normal_table_detail_and_export(self):
        original = {"genome": {"accession": "GCF_123", "length": 42}, "biosample": {"id": "SAMN1"}}
        archive = ArchiveResponse(gzip.compress((json.dumps(original) + "\n").encode()))
        with patch("ncbi_import.urlopen", side_effect=self.archive_opener(archive)) as opened:
            status_url = self.start()
            job = self.finished(status_url)
        self.assertEqual(job["state"], "complete")
        self.assertEqual(job["records"], 1)
        self.assertEqual(job["downloaded"], int(archive.headers["Content-Length"]))
        self.assertTrue(archive.closed)
        self.assertNotIn(-1, archive.read_sizes)
        self.assertEqual(opened.call_args.args[0].full_url, NCBI_URL)
        self.assertEqual(opened.call_args.kwargs["timeout"], 60)
        status = self.client.get(status_url)
        self.assertIn(b"Explore imported records", status.data)
        self.assertNotIn(b'http-equiv="refresh"', status.data)
        self.assertEqual(status.headers["Cache-Control"], "no-store")
        dataset_url = f'/datasets/{job["dataset_id"]}'
        table = self.client.get(dataset_url)
        self.assertIn(b"GCF_123", table.data)
        self.assertIn(b"Import Brad's data", table.data)
        self.assertEqual(json.loads(self.client.get(dataset_url + "/export").data), original)
        self.assertIn(NCBI_URL.encode(), self.client.get(dataset_url + "/records/1").data)

    def test_bad_gzip_truncated_gzip_and_invalid_json_roll_back(self):
        data = b'{"valid": 1}\n' * 1100
        valid = gzip.compress(data)
        corrupt_crc = bytearray(valid)
        corrupt_crc[-8] ^= 0xFF
        for body in [b"not gzip", valid[:-6], bytes(corrupt_crc), gzip.compress(data + b"not json\n")]:
            with self.subTest(body_length=len(body)):
                with patch("ncbi_import.urlopen", side_effect=self.archive_opener(ArchiveResponse(body))):
                    url = self.start()
                    job = self.finished(url)
                self.assertEqual(job["state"], "failed")
                self.assertIn(b"No partial dataset was saved", self.client.get(url).data)
                self.assert_no_partial_dataset()

    def test_short_http_response_rolls_back_even_if_gzip_member_is_valid(self):
        body = gzip.compress(b'{"a": 1}\n')
        with patch("ncbi_import.urlopen", side_effect=self.archive_opener(ArchiveResponse(body, len(body) + 100))):
            with patch("database.import_jsonl") as imported:
                job = self.finished(self.start())
                imported.assert_not_called()
        self.assertEqual(job["state"], "failed")
        self.assert_no_partial_dataset()

    def test_network_and_http_errors_allow_retry(self):
        for error in [URLError("offline"), TimeoutError(), HTTPError(NCBI_URL, 404, "Not found", {}, None)]:
            with patch("ncbi_import.urlopen", side_effect=error):
                job = self.finished(self.start())
            self.assertEqual(job["state"], "failed")
            self.assert_no_partial_dataset()

    def test_duplicate_clicks_share_job_and_progress_is_readable_during_write(self):
        entered, release = threading.Event(), threading.Event()
        real_import = database.import_jsonl

        def paused_import(connection, stream, name, source, on_progress):
            def progress(count):
                on_progress(count)
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("Test failed to release the importer")
            return real_import(connection, stream, name, source, on_progress=progress)

        with patch("ncbi_import.urlopen", side_effect=self.archive_opener(ArchiveResponse(gzip.compress(b'{"a": 1}\n')))) as opened:
            with patch("database.import_jsonl", side_effect=paused_import):
                url = self.start()
                try:
                    self.assertTrue(entered.wait(2))
                    self.assertEqual(self.start(), url)
                    page = self.client.get(url)
                    self.assertEqual(page.status_code, 200)
                    self.assertIn(b'http-equiv="refresh"', page.data)
                    self.assertIn(b"Download finished", page.data)
                    self.assertTrue((self.jobs.cache_dir / NCBI_FILENAME).exists())
                    # No partial dataset is exposed while the writer transaction is open.
                    self.assert_no_partial_dataset()
                    self.assertEqual(opened.call_count, 2)  # HEAD then download, shared by both clicks.
                finally:
                    release.set()
                    self.finished(url)

    def test_status_missing_after_server_restart_is_explained(self):
        response = self.client.get("/imports/ncbi/unknown")
        self.assertEqual(response.status_code, 404)
        self.assertIn(b"server restarted", response.data)

    def test_jsonl_failure_keeps_download_for_retry(self):
        body = gzip.compress(b'{"a": 1}\ninvalid json\n')
        with patch("ncbi_import.urlopen", side_effect=self.archive_opener(ArchiveResponse(body))):
            job = self.finished(self.start())
        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["phase"], "importing")
        self.assertEqual((self.jobs.cache_dir / NCBI_FILENAME).read_bytes(), body)
        def only_head(request, timeout):
            self.assertEqual(request.get_method(), "HEAD")
            return ArchiveResponse(b"", advertised_length=len(body))
        with patch("ncbi_import.urlopen", side_effect=only_head) as opened:
            job = self.finished(self.start())
        self.assertEqual(job["state"], "failed")
        opened.assert_called_once()
        self.assert_no_partial_dataset()

    def test_all_error_categories_include_tracebacks_in_file_and_terminal(self):
        errors = [
            URLError("network disconnected"), TimeoutError("read timed out"),
            HTTPError(NCBI_URL, 503, "Service unavailable", {}, None),
            gzip.BadGzipFile("invalid header"), EOFError("incomplete archive"),
            database.DataError("Line 6746: Record exceeds the 8 MiB line limit."),
            sqlite3.OperationalError("database or disk is full"), RuntimeError("unexpected test failure"),
        ]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                with patch("ncbi_import.urlopen", side_effect=error):
                    url = self.start()
                    job = self.finished(url)
                log = self.jobs.log_path.read_text()
                self.assertEqual(job["state"], "failed")
                self.assertIn(str(error), job["error"])
                self.assertIn("Traceback (most recent call last)", log)
                self.assertIn("Traceback (most recent call last)", self.stderr.getvalue())
                self.assertIn(f"{type(error).__name__}: {error}", log)
                self.assertIn(f"{type(error).__name__}: {error}", self.stderr.getvalue())
                self.assertIn(f'job={job["id"]} FAILED', log)
                self.assertIn("compressed_bytes=0/None records_reported=0", log)
                self.assertIsNotNone(job["finished_at"])
                self.assertIn(b"Error details", self.client.get(url).data)
        # Creating another app instance does not discard diagnostics from old jobs.
        restarted = create_app({"TESTING": True, "DATABASE": self.path})
        self.assertIn("unexpected test failure", restarted.extensions["ncbi_imports"].log_path.read_text())


if __name__ == "__main__":
    unittest.main()
