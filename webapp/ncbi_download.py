"""Download a fixed archive to disk, resuming only a matching remote version."""
import json
import re
from time import sleep
from http.client import HTTPException, IncompleteRead
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request
from urllib.parse import urlsplit

CHUNK_SIZE = 1024 * 1024
ATTEMPTS = 4
HEADERS = {"User-Agent": "EngineExplorer/1.0", "Accept-Encoding": "identity"}


class DownloadError(RuntimeError):
    pass


class InterruptedDownload(DownloadError):
    pass


def metadata(url, headers):
    length = headers.get("Content-Length", "")
    if not length.isdecimal() or int(length) <= 0:
        raise DownloadError("NCBI did not provide a valid archive size; refusing an unverifiable download.")
    if headers.get("Content-Encoding", "identity") != "identity":
        raise DownloadError("Unexpected HTTP content encoding; cannot safely resume the archive.")
    etag = headers.get("ETag")
    validator = etag if etag and not etag.startswith("W/") else headers.get("Last-Modified")
    return {"url": url, "total": int(length), "validator": validator, "etag": etag, "last_modified": headers.get("Last-Modified")}


def save_metadata(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(path)


def cache_paths(directory, url):
    filename = urlsplit(url).path.rsplit("/", 1)[-1]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.jsonl\.gz", filename):
        raise DownloadError("The archive URL must end in a JSONL gzip filename.")
    directory = Path(directory)
    return (
        directory / filename,
        directory / (filename + ".part"),
        directory / (filename.removesuffix(".jsonl.gz") + ".metadata.json"),
    )


def invalidate_archive(directory, url):
    # Retain the file for diagnosis, but never reuse it after a gzip integrity error.
    cache_paths(directory, url)[2].unlink(missing_ok=True)


def download_archive(url, directory, update, logger, opener):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    archive, partial, info_path = cache_paths(directory, url)

    for attempt in range(ATTEMPTS):
        try:
            with opener(Request(url, headers=HEADERS, method="HEAD"), timeout=60) as response:
                current = metadata(url, response.headers)
            try:
                previous = json.loads(info_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, ValueError):
                previous = None
            same_version = bool(current["validator"]) and previous == current
            total = current["total"]
            logger.info("archive metadata content_length=%s last_modified=%s etag=%s", total, current["last_modified"], current["etag"])
            update(total=total, phase="downloading")
            if same_version and archive.exists() and archive.stat().st_size == total:
                logger.info("Reusing complete cached archive: %s", archive)
                update(downloaded=total, message="Using the previously downloaded archive…")
                return archive
            # This directory contains only app-managed downloads, never imported datasets.
            if not same_version:
                partial.write_bytes(b"")
                archive.unlink(missing_ok=True)
            offset = partial.stat().st_size if partial.exists() else 0
            if offset > total:
                partial.write_bytes(b"")
                offset = 0
            save_metadata(info_path, current)
            update(downloaded=offset, message="Downloading the compressed NCBI archive…")
            if offset == total:
                partial.replace(archive)
                return archive

            headers = dict(HEADERS)
            if offset:
                headers.update({"Range": f"bytes={offset}-", "If-Range": current["validator"]})
                logger.info("Resuming archive at compressed byte %s", offset)
            with opener(Request(url, headers=headers), timeout=60) as response:
                if response.status == 200:
                    # Range was ignored, or If-Range detected a changed source: replace, never append.
                    current = metadata(url, response.headers)
                    total = current["total"]
                    offset = 0
                    # Truncate before updating metadata, so interruption cannot relabel old bytes.
                    partial.write_bytes(b"")
                    archive.unlink(missing_ok=True)
                    save_metadata(info_path, current)
                    end = total - 1
                elif response.status == 206:
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                    if not match:
                        raise DownloadError("NCBI returned an invalid Content-Range header.")
                    start, end, remote_total = map(int, match.groups())
                    if start != offset or remote_total != total or end < start or end >= total:
                        raise DownloadError("NCBI returned a byte range that does not match the saved archive.")
                    response_validator = response.headers.get("ETag") if current["etag"] and not current["etag"].startswith("W/") else response.headers.get("Last-Modified")
                    if response_validator != current["validator"]:
                        raise DownloadError("The remote archive changed during resume. Retry to fetch its new version.")
                    if response.headers.get("Content-Length") != str(end - start + 1):
                        raise DownloadError("NCBI returned a range with an inconsistent length.")
                    if response.headers.get("Content-Encoding", "identity") != "identity":
                        raise DownloadError("Unexpected HTTP encoding on a resumed download.")
                else:
                    raise DownloadError(f"Unexpected download HTTP status: {response.status}")
                update(total=total, downloaded=offset)
                with partial.open("ab") as output:
                    received = offset
                    while True:
                        try:
                            chunk = response.read(CHUNK_SIZE)
                        except IncompleteRead as error:
                            if error.partial:
                                if received + len(error.partial) > end + 1:
                                    raise DownloadError("The download exceeded its advertised byte range.") from error
                                output.write(error.partial)
                                received += len(error.partial)
                                update(downloaded=received)
                            raise
                        if not chunk:
                            break
                        if received + len(chunk) > end + 1:
                            raise DownloadError("The download exceeded its advertised byte range.")
                        output.write(chunk)
                        received += len(chunk)
                        update(downloaded=received)
                    if received != total:
                        raise InterruptedDownload(f"HTTP download stopped at {received:,} of {total:,} bytes. Partial download saved for resume.")
            partial.replace(archive)
            logger.info("Archive download complete: %s bytes at %s", total, archive)
            return archive
        except (URLError, TimeoutError, ConnectionError, HTTPException, InterruptedDownload) as error:
            retryable = not isinstance(error, HTTPError) or error.code in {408, 429, 500, 502, 503, 504}
            if isinstance(error, HTTPError):
                error.close()
            if not retryable:
                raise
            if attempt == ATTEMPTS - 1:
                raise DownloadError(f"Download failed after {ATTEMPTS} attempts. Any partial archive is saved; retry to resume. Last error: {error}") from error
            delay = 2 ** attempt
            logger.warning("Download attempt %s interrupted; retrying in %ss: %s", attempt + 1, delay, error)
            update(message=f"Download interrupted. Retrying in {delay} seconds; saved bytes will be reused if the source is unchanged…")
            sleep(delay)
