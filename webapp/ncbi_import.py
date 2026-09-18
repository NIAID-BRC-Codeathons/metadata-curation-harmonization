"""One background NCBI import at a time, within the existing app process."""
import gzip
import logging
import secrets
import sqlite3
import threading
import time
import zlib
from contextlib import closing
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import database
from ncbi_download import DownloadError, download_archive, invalidate_archive

NCBI_URL = "https://ftp.ncbi.nlm.nih.gov/pub/datasets/.argonne/combined.v2.jsonl.gz"
NCBI_FILENAME = NCBI_URL.rsplit("/", 1)[-1]


class ImportJobs:
    def __init__(self, database_path):
        self.database_path = database_path
        self.lock = threading.Lock()
        self.jobs = {}
        self.active_id = None
        self.log_path = Path(database_path).resolve().parent / "ncbi-import.log"
        self.cache_dir = self.log_path.parent / "ncbi-cache"

    def snapshot(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            return dict(job) if job else None

    def start(self):
        with self.lock:
            if self.active_id is not None:
                return self.active_id
            job_id = secrets.token_hex(16)
            self.jobs[job_id] = dict(
                id=job_id, state="running", message="Connecting to NCBI…",
                downloaded=0, total=None, records=0, dataset_id=None,
                phase="downloading",
                error=None, log_path=str(self.log_path),
                started_at=datetime.now(timezone.utc).isoformat(), finished_at=None,
            )
            self.active_id = job_id
            # Retain a small history of completed jobs for links/back navigation.
            while len(self.jobs) > 10:
                del self.jobs[next(iter(self.jobs))]
            worker = threading.Thread(target=self.run, args=(job_id,), name="ncbi-import", daemon=True)
            worker.start()
            return job_id

    def update(self, job_id, **changes):
        with self.lock:
            self.jobs[job_id].update(changes)

    def run(self, job_id):
        # Per-worker handlers avoid duplicates across app instances and close on exit.
        logger = logging.Logger("ncbi_import", level=logging.INFO)
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        for handler in (
            logging.StreamHandler(),
            RotatingFileHandler(self.log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8", delay=True),
        ):
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        started = time.monotonic()
        last_log = started

        def update(**changes):
            nonlocal last_log
            self.update(job_id, **changes)
            now = time.monotonic()
            if now - last_log >= 30:
                job = self.snapshot(job_id)
                logger.info("job=%s phase=%s compressed_bytes=%s/%s records_reported=%s elapsed_s=%.1f", job_id, job["phase"], job["downloaded"], job["total"], job["records"], now - started)
                last_log = now

        try:
            logger.info("job=%s started source=%s database=%s", job_id, NCBI_URL, self.database_path)
            archive = download_archive(NCBI_URL, self.cache_dir, update, logger, opener=urlopen)
            update(phase="importing", message="Download complete. Decompressing the local archive and importing records into SQLite…")
            logger.info("job=%s phase=importing archive=%s", job_id, archive)
            with closing(database.connect(self.database_path)) as connection:
                with gzip.open(archive, "rb") as stream:
                    dataset_id = database.import_jsonl(
                        connection, stream, "NCBI Argonne · combined v2", NCBI_URL,
                        on_progress=lambda count: update(records=count),
                    )
            update(state="complete", phase="complete", message="Import complete. Your records are ready to explore.", dataset_id=dataset_id)
            logger.info("job=%s complete dataset_id=%s compressed_bytes=%s records=%s elapsed_s=%.1f", job_id, dataset_id, archive.stat().st_size, self.snapshot(job_id)["records"], time.monotonic() - started)
        except Exception as error:
            job = self.snapshot(job_id)
            logger.exception("job=%s FAILED phase=%s compressed_bytes=%s/%s records_reported=%s elapsed_s=%.1f", job_id, job["phase"], job["downloaded"], job["total"], job["records"], time.monotonic() - started)
            if isinstance(error, DownloadError):
                message = str(error)
            elif isinstance(error, HTTPError):
                message = f"NCBI returned HTTP {error.code}. Please try again later."
                error.close()
            elif isinstance(error, (URLError, TimeoutError)):
                message = "Could not download the NCBI file. Check the server’s internet connection and try again."
            elif isinstance(error, (gzip.BadGzipFile, EOFError, zlib.error)):
                invalidate_archive(self.cache_dir, NCBI_URL)
                message = "The cached NCBI archive failed gzip validation. Retry to download a fresh copy."
            elif isinstance(error, database.DataError):
                message = f"The NCBI JSONL could not be imported: {error}"
            elif isinstance(error, (OSError, sqlite3.Error)):
                message = "The import was interrupted. Check the server’s connection, available disk space, and database access before retrying."
            else:
                message = "The import could not finish. Check the server log for details."
            update(state="failed", message=message + " No partial dataset was saved.", error=f"{type(error).__name__}: {error}")
        finally:
            self.update(job_id, finished_at=datetime.now(timezone.utc).isoformat())
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)
            with self.lock:
                self.active_id = None
