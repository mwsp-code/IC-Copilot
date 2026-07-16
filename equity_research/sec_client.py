from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import config
from .models import CompanyIdentity, FilingRecord


SEC_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_SUBMISSIONS_ARCHIVE_URL = "https://data.sec.gov/submissions/{name}"
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{doc}"


class SecClientError(RuntimeError):
    pass


class SecClient:
    def __init__(
        self,
        user_agent: str | None = None,
        cache_dir: Path | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self.user_agent = user_agent or config.SEC_USER_AGENT
        self.cache_dir = cache_dir or config.CACHE_DIR
        self.timeout_seconds = timeout_seconds or config.REQUEST_TIMEOUT_SECONDS
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._text_memory_cache: dict[str, str] = {}
        self._json_memory_cache: dict[str, dict] = {}
        self._lock_registry_guard = Lock()
        self._url_locks: dict[str, Lock] = {}

    def map_ticker(self, ticker: str) -> CompanyIdentity:
        ticker = ticker.upper().strip()
        ticker_map = self.get_json(SEC_TICKER_URL, ttl_seconds=24 * 60 * 60)
        for row in ticker_map.values():
            if row.get("ticker", "").upper() == ticker:
                cik = str(row["cik_str"]).zfill(10)
                return CompanyIdentity(ticker=ticker, cik=cik, name=row["title"])
        raise SecClientError(f"Ticker {ticker!r} was not found in the SEC ticker map.")

    def get_submissions(self, cik: str) -> dict:
        return self.get_json(SEC_SUBMISSIONS_URL.format(cik=cik), ttl_seconds=20 * 60)

    def get_company_facts(self, cik: str) -> dict:
        return self.get_json(SEC_FACTS_URL.format(cik=cik), ttl_seconds=60 * 60)

    def get_recent_filings(
        self,
        cik: str,
        forms: set[str] | None = None,
        limit: int = 12,
    ) -> list[FilingRecord]:
        submissions = self.get_submissions(cik)
        recent = submissions.get("filings", {}).get("recent", {})
        forms_filter = {form.upper() for form in forms} if forms else None
        filings: list[FilingRecord] = []
        accession_numbers = recent.get("accessionNumber", [])
        cik_int = str(int(cik))

        for idx, accession in enumerate(accession_numbers):
            form = _safe_idx(recent.get("form", []), idx, "")
            if forms_filter and form.upper() not in forms_filter:
                continue
            primary_doc = _safe_idx(recent.get("primaryDocument", []), idx, "")
            if not primary_doc:
                continue
            archive_accession = accession.replace("-", "")
            filings.append(
                FilingRecord(
                    form=form,
                    accession=accession,
                    filing_date=_safe_idx(recent.get("filingDate", []), idx, ""),
                    report_date=_safe_idx(recent.get("reportDate", []), idx, ""),
                    primary_doc=primary_doc,
                    description=_safe_idx(
                        recent.get("primaryDocDescription", []), idx, ""
                    ),
                    url=SEC_ARCHIVES_URL.format(
                        cik_int=cik_int,
                        accession=archive_accession,
                        doc=primary_doc,
                    ),
                    accepted_at=_safe_idx(recent.get("acceptanceDateTime", []), idx, "") or None,
                )
            )
            if len(filings) >= limit:
                break
        return filings

    def get_comparable_filings(
        self,
        cik: str,
        form: str,
        limit: int = 4,
    ) -> list[FilingRecord]:
        """Return same-form filings, expanding into SEC submission archives when needed."""
        filings = self.get_recent_filings(cik, forms={form}, limit=limit)
        if len(filings) >= limit:
            return filings
        submissions = self.get_submissions(cik)
        seen = {item.accession for item in filings}
        for archive_meta in submissions.get("filings", {}).get("files", []):
            name = str(archive_meta.get("name") or "")
            if not name:
                continue
            archive = self.get_json(
                SEC_SUBMISSIONS_ARCHIVE_URL.format(name=name),
                ttl_seconds=24 * 60 * 60,
            )
            for item in _filing_records_from_columns(archive, cik, {form.upper()}):
                if item.accession in seen:
                    continue
                filings.append(item)
                seen.add(item.accession)
                if len(filings) >= limit:
                    break
            if len(filings) >= limit:
                break
        return sorted(
            filings,
            key=lambda item: (item.report_date or item.filing_date, item.filing_date),
            reverse=True,
        )[:limit]

    def get_filing_text(self, filing: FilingRecord) -> str:
        return self.get_text(filing.url, ttl_seconds=7 * 24 * 60 * 60)

    def get_json(self, url: str, ttl_seconds: int) -> dict:
        cached = self._json_memory_cache.get(url)
        if cached is not None:
            return cached
        with self._url_lock(f"json:{url}"):
            cached = self._json_memory_cache.get(url)
            if cached is not None:
                return cached
            text = self.get_text(url, ttl_seconds=ttl_seconds)
            payload = json.loads(text)
            self._json_memory_cache[url] = payload
            return payload

    def get_text(self, url: str, ttl_seconds: int) -> str:
        cached = self._text_memory_cache.get(url)
        if cached is not None:
            return cached
        with self._url_lock(url):
            cached = self._text_memory_cache.get(url)
            if cached is not None:
                return cached
            cache_path = self._cache_path(url)
            if cache_path.exists() and time.time() - cache_path.stat().st_mtime < ttl_seconds:
                text = cache_path.read_text(encoding="utf-8", errors="ignore")
                self._text_memory_cache[url] = text
                return text

            req = Request(
                url,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "application/json,text/html,text/plain,*/*",
                },
            )
            try:
                with urlopen(req, timeout=self.timeout_seconds) as response:
                    raw = response.read()
                    encoding = response.headers.get_content_charset() or "utf-8"
                    text = raw.decode(encoding, errors="replace")
            except (HTTPError, URLError, TimeoutError) as exc:
                if cache_path.exists():
                    text = cache_path.read_text(encoding="utf-8", errors="ignore")
                    self._text_memory_cache[url] = text
                    return text
                raise SecClientError(f"Could not fetch {url}: {exc}") from exc

            cache_path.write_text(text, encoding="utf-8")
            self._text_memory_cache[url] = text
            return text

    def _url_lock(self, url: str) -> Lock:
        with self._lock_registry_guard:
            lock = self._url_locks.get(url)
            if lock is None:
                lock = Lock()
                self._url_locks[url] = lock
            return lock

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.cache"


def _safe_idx(values: list, idx: int, default: str) -> str:
    try:
        value = values[idx]
    except IndexError:
        return default
    return "" if value is None else str(value)


def _filing_records_from_columns(
    rows: dict,
    cik: str,
    forms_filter: set[str] | None = None,
) -> list[FilingRecord]:
    records: list[FilingRecord] = []
    cik_int = str(int(cik))
    for idx, accession in enumerate(rows.get("accessionNumber", [])):
        form = _safe_idx(rows.get("form", []), idx, "")
        if forms_filter and form.upper() not in forms_filter:
            continue
        primary_doc = _safe_idx(rows.get("primaryDocument", []), idx, "")
        if not primary_doc:
            continue
        records.append(FilingRecord(
            form=form,
            accession=str(accession),
            filing_date=_safe_idx(rows.get("filingDate", []), idx, ""),
            report_date=_safe_idx(rows.get("reportDate", []), idx, ""),
            primary_doc=primary_doc,
            description=_safe_idx(rows.get("primaryDocDescription", []), idx, ""),
            url=SEC_ARCHIVES_URL.format(
                cik_int=cik_int,
                accession=str(accession).replace("-", ""),
                doc=primary_doc,
            ),
            accepted_at=_safe_idx(rows.get("acceptanceDateTime", []), idx, "") or None,
        ))
    return records
