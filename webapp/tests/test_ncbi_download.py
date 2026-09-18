import io
import json
import logging
import tempfile
import unittest
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from ncbi_download import DownloadError, download_archive

URL = "https://example.test/combined.jsonl.gz"


class Response(io.BytesIO):
    def __init__(self, body=b"", *, total=None, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = {"Content-Length": str(len(body) if total is None else total), "ETag": '"version-1"'}
        self.headers.update(headers or {})


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cache = Path(self.temp.name)
        self.logger = logging.Logger("test-download")
        self.logger.addHandler(logging.NullHandler())
        self.progress = {}
        self.sleep_patch = patch("ncbi_download.sleep")
        self.sleep_patch.start()

    def tearDown(self):
        self.sleep_patch.stop()
        self.temp.cleanup()

    def download(self, opener):
        return download_archive(URL, self.cache, self.progress.update, self.logger, opener)

    def seed_partial(self, data, total, validator='"version-1"'):
        (self.cache / "combined.jsonl.gz.part").write_bytes(data)
        (self.cache / "combined.metadata.json").write_text(json.dumps({"url": URL, "total": total, "validator": validator, "etag": validator, "last_modified": None}))

    def test_early_eof_resumes_exact_offset_and_produces_identical_file(self):
        content = b"0123456789abcdef"
        gets = []
        def opener(request, timeout):
            self.assertEqual(timeout, 60)
            if request.get_method() == "HEAD":
                return Response(total=len(content))
            gets.append(request)
            if len(gets) == 1:
                return Response(content[:5], total=len(content))
            self.assertEqual(request.get_header("Range"), "bytes=5-")
            self.assertEqual(request.get_header("If-range"), '"version-1"')
            return Response(content[5:], status=206, headers={"Content-Range": "bytes 5-15/16"})
        path = self.download(opener)
        self.assertEqual(path.read_bytes(), content)
        self.assertEqual(self.progress["downloaded"], len(content))
        self.assertEqual(len(gets), 2)
        self.assertFalse((self.cache / "combined.jsonl.gz.part").exists())

    def test_partial_survives_failed_job_and_resumes_on_next_call(self):
        def disconnected(request, timeout):
            return Response(total=8) if request.get_method() == "HEAD" else Response(b"abcd", total=8)
        with patch("ncbi_download.ATTEMPTS", 1):
            with self.assertRaisesRegex(DownloadError, "partial archive is saved"):
                self.download(disconnected)
        self.assertEqual((self.cache / "combined.jsonl.gz.part").read_bytes(), b"abcd")
        def resumed(request, timeout):
            if request.get_method() == "HEAD":
                return Response(total=8)
            self.assertEqual(request.get_header("Range"), "bytes=4-")
            return Response(b"efgh", status=206, headers={"Content-Range": "bytes 4-7/8"})
        self.assertEqual(self.download(resumed).read_bytes(), b"abcdefgh")

    def test_changed_remote_version_discards_old_partial(self):
        self.seed_partial(b"old", 8)
        def opener(request, timeout):
            if request.get_method() == "HEAD":
                return Response(total=8, headers={"ETag": '"version-2"'})
            self.assertIsNone(request.get_header("Range"))
            return Response(b"new-file", headers={"ETag": '"version-2"'})
        self.assertEqual(self.download(opener).read_bytes(), b"new-file")

    def test_server_ignoring_range_replaces_instead_of_appending(self):
        self.seed_partial(b"abcd", 8)
        def opener(request, timeout):
            if request.get_method() == "HEAD":
                return Response(total=8)
            self.assertEqual(request.get_header("Range"), "bytes=4-")
            return Response(b"abcdefgh")
        self.assertEqual(self.download(opener).read_bytes(), b"abcdefgh")

    def test_invalid_range_cannot_corrupt_saved_prefix(self):
        self.seed_partial(b"abcd", 8)
        def opener(request, timeout):
            if request.get_method() == "HEAD":
                return Response(total=8)
            return Response(b"fgh", status=206, headers={"Content-Range": "bytes 5-7/8"})
        with self.assertRaisesRegex(DownloadError, "does not match"):
            self.download(opener)
        self.assertEqual((self.cache / "combined.jsonl.gz.part").read_bytes(), b"abcd")

    def test_complete_cache_requires_matching_version_and_avoids_get(self):
        self.seed_partial(b"abcdefgh", 8)
        def only_head(request, timeout):
            self.assertEqual(request.get_method(), "HEAD")
            return Response(total=8)
        path = self.download(only_head)
        self.assertEqual(path.read_bytes(), b"abcdefgh")
        self.assertEqual(self.download(only_head).read_bytes(), b"abcdefgh")

    def test_incomplete_read_preserves_received_bytes_for_resume(self):
        class BrokenResponse(Response):
            def read(self, size=-1):
                raise IncompleteRead(b"abc", 5)
        def opener(request, timeout):
            return Response(total=8) if request.get_method() == "HEAD" else BrokenResponse(total=8)
        with patch("ncbi_download.ATTEMPTS", 1):
            with self.assertRaises(DownloadError):
                self.download(opener)
        self.assertEqual((self.cache / "combined.jsonl.gz.part").read_bytes(), b"abc")
        self.assertEqual(self.progress["downloaded"], 3)

    def test_permanent_http_errors_are_not_retried(self):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            raise HTTPError(URL, 404, "Not found", {}, None)
        with self.assertRaises(HTTPError):
            self.download(opener)
        self.assertEqual(len(calls), 1)

    def test_missing_validator_never_reuses_partial_bytes(self):
        self.seed_partial(b"OLD", 8)
        def opener(request, timeout):
            response = Response(total=8) if request.get_method() == "HEAD" else Response(b"new-file")
            del response.headers["ETag"]
            self.assertIsNone(request.get_header("Range"))
            return response
        self.assertEqual(self.download(opener).read_bytes(), b"new-file")

    def test_v2_does_not_reuse_v1_cache_even_with_matching_size_and_validator(self):
        self.seed_partial(b"original", 8)
        def original_head(request, timeout):
            self.assertEqual(request.get_method(), "HEAD")
            return Response(total=8)
        original = self.download(original_head)
        version2_url = "https://example.test/combined.v2.jsonl.gz"
        def version2(request, timeout):
            self.assertEqual(request.full_url, version2_url)
            self.assertIsNone(request.get_header("Range"))
            return Response(total=8) if request.get_method() == "HEAD" else Response(b"version2")
        updated = download_archive(version2_url, self.cache, self.progress.update, self.logger, version2)
        self.assertEqual(updated.name, "combined.v2.jsonl.gz")
        self.assertEqual(updated.read_bytes(), b"version2")
        self.assertEqual(original.read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()
