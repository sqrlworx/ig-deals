"""Tiny HTTP helper (stdlib only): honest UA, global throttle, shared 429 cooldown, per-day disk cache.

Shopify rate-limits per client IP across *all* stores, so requests are throttled globally
(not per domain) and a 429 on any store pauses every worker until its Retry-After elapses.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

UA = "Mozilla/5.0 (compatible; ig-deals-scanner/1.0; personal deal research)"
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip"}

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"
MIN_INTERVAL = 0.8  # seconds between any two network requests (all threads)
MAX_429_RETRIES = 3
CACHE_ENABLED = True  # flipped off by `run.py --no-cache`

_lock = threading.Lock()
_next_slot = 0.0
_cooldown_until = 0.0


def _wait_turn():
    global _next_slot
    with _lock:
        now = time.monotonic()
        start = max(now, _next_slot, _cooldown_until)
        _next_slot = start + MIN_INTERVAL
    delay = start - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def _cool_down(seconds: float):
    global _cooldown_until
    with _lock:
        _cooldown_until = max(_cooldown_until, time.monotonic() + seconds)


CURL = shutil.which("curl")


class _HTTPStatus(Exception):
    def __init__(self, code: int, retry_after: str | None = None):
        super().__init__(code)
        self.code, self.retry_after = code, retry_after


def _get_curl(url: str, timeout: int, accept: str | None = None) -> bytes:
    """curl transport (preferred: Shopify's edge throttles Python's urllib client far more aggressively)."""
    cmd = [CURL, "-sS", "-L", "--compressed", "-m", str(timeout), "-A", UA,
           "-H", f"Accept-Language: {HEADERS['Accept-Language']}",
           *(["-H", f"Accept: {accept}"] if accept else []),
           "-D", "-", "-o", "-", "-w", "\n%{http_code}", url]
    # write headers + body to stdout; status code trails on the last line
    out = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
    if out.returncode != 0:
        raise OSError(out.stderr.decode(errors="replace")[:200])
    raw, _, code = out.stdout.rpartition(b"\n")
    status = int(code or 0)
    # strip (possibly multiple, after redirects) header blocks
    body = raw
    while body.startswith(b"HTTP/"):
        sep = body.find(b"\r\n\r\n")
        if sep < 0:
            break
        head, body = body[:sep], body[sep + 4:]
    if status != 200:
        ra = None
        for line in head.decode(errors="replace").splitlines():
            if line.lower().startswith("retry-after:"):
                ra = line.split(":", 1)[1].strip()
        raise _HTTPStatus(status, ra)
    return body


def _get_urllib(url: str, timeout: int, accept: str | None = None) -> bytes:
    hdrs = {**HEADERS, **({"Accept": accept} if accept else {})}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=hdrs), timeout=timeout) as r:
            raw = r.read()
            return gzip.decompress(raw) if r.headers.get("Content-Encoding") == "gzip" else raw
    except urllib.error.HTTPError as e:
        raise _HTTPStatus(e.code, e.headers.get("Retry-After")) from None


def _cache_path(url: str, accept: str | None = None) -> Path:
    key = url + (f"|{accept}" if accept else "")
    return CACHE_DIR / date.today().isoformat() / hashlib.sha1(key.encode()).hexdigest()[:20]


def prune_cache(keep_days: int = 2):
    """Drop cache folders older than `keep_days` days (cache is keyed by date)."""
    if not CACHE_DIR.exists():
        return
    days = sorted(d for d in CACHE_DIR.iterdir() if d.is_dir())
    for d in days[:-keep_days]:
        for f in d.iterdir():
            f.unlink()
        d.rmdir()


def fetch(url: str, *, binary: bool = False, timeout: int = 25, retries: int = 2,
          use_cache: bool | None = None, accept: str | None = None) -> tuple[int, bytes | str]:
    """Return (status, body). status=0 means a network-level failure."""
    if use_cache is None:
        use_cache = CACHE_ENABLED
    cp = _cache_path(url, accept)
    if use_cache and cp.exists():
        raw = cp.read_bytes()
        return 200, raw if binary else raw.decode("utf-8", "replace")

    last_status, n429, attempt = 0, 0, 0
    while attempt <= retries:
        _wait_turn()
        try:
            raw = _get_curl(url, timeout, accept) if CURL else _get_urllib(url, timeout, accept)
            if use_cache:
                cp.parent.mkdir(parents=True, exist_ok=True)
                cp.write_bytes(raw)
            return 200, raw if binary else raw.decode("utf-8", "replace")
        except _HTTPStatus as e:
            last_status = e.code
            if e.code in (401, 403, 404, 410):
                break
            if e.code == 429:
                n429 += 1
                if n429 > MAX_429_RETRIES:
                    break
                try:
                    wait = float(e.retry_after or 0)
                except ValueError:
                    wait = 0
                _cool_down(max(wait, 10.0 * n429))
                continue  # 429s don't consume normal retries
        except Exception:  # noqa: BLE001 - DNS, resets, timeouts
            last_status = 0
        attempt += 1
        time.sleep(1.5 * attempt)
    return last_status, b"" if binary else ""


def fetch_json(url: str, **kw):
    status, body = fetch(url, **kw)
    if status != 200 or not body:
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None
