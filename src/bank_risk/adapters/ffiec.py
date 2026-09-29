from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from bank_risk.models import CallReport

FFIEC_BASE_URL = "https://ffieccdr.azure-api.us/public"


def _clean_key(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"NA", "N/A", "NULL", "."}:
        return None
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1]}"
    try:
        return float(text)
    except ValueError:
        return None


def parse_sdf_text(text: str) -> list[dict[str, str]]:
    """Parse common FFIEC SDF layouts (wide or long) into MDRM-keyed rows."""
    sample = text[:8192]
    delimiter = ";" if sample.count(";") >= sample.count("\t") else "\t"
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    raw_rows = [dict(row) for row in reader if row]
    if not raw_rows:
        return []
    normalized_headers = {_clean_key(key): key for key in raw_rows[0]}
    mdrm_col = next((normalized_headers[k] for k in normalized_headers if "MDRM" in k), None)
    value_col = next(
        (
            normalized_headers[k]
            for k in normalized_headers
            if k in {"VALUE", "AMOUNT", "DATA", "DATAVALUE"}
        ),
        None,
    )
    if mdrm_col and value_col:
        record: dict[str, str] = {}
        for row in raw_rows:
            mdrm = _clean_key(row.get(mdrm_col, ""))
            if mdrm:
                record[mdrm] = row.get(value_col, "")
        return [record]
    return [{_clean_key(key): str(value or "") for key, value in row.items()} for row in raw_rows]


def _lookup(row: dict[str, str], *codes: str) -> float | None:
    normalized = {_clean_key(key): value for key, value in row.items()}
    for code in codes:
        key = _clean_key(code)
        if key in normalized:
            return _number(normalized[key])
        suffix = key[-4:]
        for candidate, value in normalized.items():
            if candidate.endswith(suffix):
                number = _number(value)
                if number is not None:
                    return number
    return None


def row_to_call_report(
    row: dict[str, str],
    *,
    report_date: date,
    source: str,
    source_url: str,
    payload_hash: str,
    retrieved_at: datetime,
    cert_override: int | None = None,
) -> CallReport | None:
    cert_value = cert_override or _lookup(row, "RSSD9050", "RCON9050", "FDICCERTNUMBER", "CERT")
    if cert_value is None:
        return None
    name = next(
        (
            value.strip()
            for key, value in row.items()
            if _clean_key(key) in {"RSSD9017", "NAME", "LEGALTITLE"} and value.strip()
        ),
        f"FDIC Certificate {int(cert_value)}",
    )
    deposit_expense_parts = [
        _lookup(row, "RIAD4508"),
        _lookup(row, "RIAD0093"),
        _lookup(row, "RIADHK03"),
        _lookup(row, "RIADHK04"),
    ]
    deposit_expense = None
    if any(value is not None for value in deposit_expense_parts):
        deposit_expense = sum(value or 0.0 for value in deposit_expense_parts)
    return CallReport(
        cert=int(cert_value),
        report_date=report_date,
        source=source,
        name=name,
        state=next((v for k, v in row.items() if _clean_key(k) in {"RSSD9200", "STATE"}), None),
        form_type=next((v for k, v in row.items() if "FILINGTYPE" in _clean_key(k)), None),
        total_assets=_lookup(row, "RCFD2170", "RCON2170"),
        total_deposits=_lookup(row, "RCFD2200", "RCON2200"),
        domestic_deposits=_lookup(row, "RCON2200", "RCFD2200"),
        bank_equity=_lookup(row, "RCON3210", "RCFD3210"),
        preferred_equity=_lookup(row, "RCON3838", "RCFD3838"),
        intangible_assets=_lookup(row, "RCFD2143", "RCON2143"),
        htm_amortized_cost=_lookup(row, "RCFD1754", "RCON1754"),
        htm_fair_value=_lookup(row, "RCFD1771", "RCON1771"),
        afs_amortized_cost=_lookup(row, "RCFD1772", "RCON1772"),
        afs_fair_value=_lookup(row, "RCFD1773", "RCON1773"),
        uninsured_deposits=_lookup(row, "RCON5597", "RCFD5597"),
        deposit_interest_expense_ytd=deposit_expense,
        source_url=source_url,
        retrieved_at=retrieved_at,
        payload_hash=payload_hash,
        raw=row,
    )


class FfiecClient:
    def __init__(
        self,
        user_id: str,
        token: str,
        cache_dir: Path,
        client: httpx.Client | None = None,
        min_interval_seconds: float = 1.5,
        max_retries: int = 4,
    ) -> None:
        if not user_id or not token:
            raise ValueError("FFIEC_USER_ID and FFIEC_TOKEN are required")
        self.user_id = user_id
        self.token = token.removeprefix("Bearer ").strip()
        self.cache_dir = cache_dir / "ffiec"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.client = client or httpx.Client(timeout=120.0, follow_redirects=True)
        self.min_interval_seconds = min_interval_seconds
        self.max_retries = max_retries
        self._last_request = 0.0

    def retrieve_sdf(self, cert: int, report_date: date, *, use_cache: bool = True) -> Path:
        cache_path = self.cache_dir / f"{cert}_{report_date:%Y%m%d}.sdf"
        if use_cache and cache_path.exists():
            return cache_path
        headers = {
            "UserID": self.user_id,
            "Authentication": f"Bearer {self.token}",
            "dataSeries": "Call",
            "reportingPeriodEndDate": report_date.strftime("%m/%d/%Y"),
            "fiIdType": "FDICCertNumber",
            "fiId": str(cert),
            "facsimileFormat": "SDF",
            "Accept": "application/json",
        }
        response: httpx.Response | None = None
        for attempt in range(self.max_retries):
            delay = self.min_interval_seconds - (time.monotonic() - self._last_request)
            if delay > 0:
                time.sleep(delay)
            try:
                response = self.client.get(
                    f"{FFIEC_BASE_URL}/RetrieveFacsimile", headers=headers
                )
            except httpx.TransportError:
                self._last_request = time.monotonic()
                if attempt == self.max_retries - 1:
                    raise
                continue
            self._last_request = time.monotonic()
            if response.status_code not in {429, 500, 502, 503, 504}:
                response.raise_for_status()
                break
            if attempt == self.max_retries - 1:
                response.raise_for_status()
        if response is None:
            raise RuntimeError("FFIEC request retries exhausted")
        content = response.content
        try:
            payload = response.json()
            if isinstance(payload, list) and all(isinstance(item, int) for item in payload):
                content = bytes(payload)
            elif isinstance(payload, dict):
                value = payload.get("FacsimileFile") or payload.get("facsimileFile")
                if isinstance(value, list):
                    content = bytes(value)
                elif isinstance(value, str):
                    content = base64.b64decode(value)
        except (json.JSONDecodeError, ValueError):
            pass
        cache_path.write_bytes(content)
        return cache_path

    def verify(self, cert: int, report_date: date) -> CallReport:
        path = self.retrieve_sdf(cert, report_date)
        content = path.read_bytes()
        rows = parse_sdf_text(content.decode("utf-8-sig", errors="replace"))
        if not rows:
            raise ValueError("FFIEC SDF contained no parseable data")
        result = row_to_call_report(
            rows[0],
            report_date=report_date,
            source="ffiec-pws",
            source_url=f"{FFIEC_BASE_URL}/RetrieveFacsimile",
            payload_hash=hashlib.sha256(content).hexdigest(),
            retrieved_at=datetime.now(UTC),
            cert_override=cert,
        )
        if result is None:
            raise ValueError("FFIEC SDF did not contain an institution record")
        return result
