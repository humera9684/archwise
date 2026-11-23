"""Extract: download AWS bulk Price List offer files (public, no credentials).

For each offer the region index names the current version file; if that
version was already downloaded, the existing raw snapshot is reused
(incremental fetching, ETL-02). Downloads stream to disk with a SHA-256
checksum and retry with exponential backoff on network errors, HTTP 429 and
5xx. `file://` base URLs work too, which is how tests run against fixtures.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("pricing_etl.extract")
USER_AGENT = "archwise-pricing-etl/1.0"
RETRYABLE_HTTP = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class RawFile:
    offer: str
    region: str
    source_uri: str
    offer_version: str | None
    local_path: Path
    sha256: str
    bytes: int
    reused: bool = False


def _open(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout)


def with_retries(fn, *, attempts: int = 5, base_delay: float = 1.0, what: str = ""):
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            if e.code not in RETRYABLE_HTTP or attempt == attempts:
                raise
            err = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == attempts:
                raise
            err = str(e)
        delay = base_delay * 2 ** (attempt - 1) * (1 + random.random() * 0.25)
        log.warning("retry what=%s attempt=%d error=%s sleep=%.1fs", what, attempt, err, delay)
        time.sleep(delay)


def fetch_json(url: str, timeout: float = 60) -> dict:
    def go():
        with _open(url, timeout) as r:
            return json.loads(r.read())
    return with_retries(go, what=url)


def download(url: str, dest_dir: Path, timeout: float = 300) -> tuple[Path, str, int]:
    """Stream `url` to dest_dir/<sha256>.json; return (path, sha256, bytes)."""
    dest_dir.mkdir(parents=True, exist_ok=True)

    def go():
        h, n = hashlib.sha256(), 0
        tmp = dest_dir / f".partial-{int(time.time() * 1000)}"
        with _open(url, timeout) as r, open(tmp, "wb") as out:
            while chunk := r.read(1 << 20):
                h.update(chunk)
                out.write(chunk)
                n += len(chunk)
        final = dest_dir / f"{h.hexdigest()}.json"
        tmp.replace(final)
        return final, h.hexdigest(), n
    return with_retries(go, what=url)


def current_version_url(base_url: str, offer: str, region: str) -> tuple[str, str | None]:
    """Resolve the current regional file for an offer via its region index."""
    index = fetch_json(f"{base_url}/offers/v1.0/aws/{offer}/current/region_index.json")
    entry = index["regions"].get(region)
    if not entry:
        raise LookupError(f"{offer} has no price file for region {region}")
    path = entry["currentVersionUrl"]
    url = path if "://" in path else f"{base_url}{path}"
    version = path.rstrip("/").split("/")[-3] if path.count("/") >= 3 else None
    return url, version


def extract_offer(base_url: str, offer: str, region: str, data_dir: Path,
                  previous: dict | None = None) -> RawFile:
    """Download (or reuse) the current regional file for one offer.

    `previous` is the last raw snapshot row for this offer/region, if any; when
    its source_uri equals the current version URL and its file still exists,
    nothing is downloaded.
    """
    url, version = current_version_url(base_url, offer, region)
    if previous and previous["source_uri"] == url and Path(previous["local_path"]).exists():
        log.info("unchanged offer=%s version=%s", offer, version)
        return RawFile(offer, region, url, version, Path(previous["local_path"]),
                       previous["sha256"], previous["bytes"], reused=True)
    t0 = time.time()
    path, sha, n = download(url, data_dir / "aws" / offer / region)
    log.info("downloaded offer=%s version=%s bytes=%d seconds=%.1f", offer, version, n, time.time() - t0)
    return RawFile(offer, region, url, version, path, sha, n)
