from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from bank_risk.dates import parse_date
from bank_risk.models import CallReport

FDIC_BASE_URL = "https://api.fdic.gov/banks/financials"
FDIC_FIELDS = [
    "CERT",
    "NAME",
    "STALP",
    "CITY",
    "REPDTE",
    "CALLFORM",
    "ASSET",
    "DEP",
    "DEPDOM",
    "EQ",
    "EQPP",
    "INTAN",
    "SCHA",
    "SCHF",
    "SCAA",
    "SC",
    "SCHTMRES",
    "DEPUNINS",
    "EDEPDOM",
    "CHBAL",
    "OTHBOR",
    "NIMYQ",
]


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class FdicClient:
    def __init__(
        self,
        cache_dir: Path,
        api_key: str | None = None,
        client: httpx.Client | None = None,
        max_retries: int = 4,
    ) -> None:
        self.cache_dir = cache_dir / "fdic"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = api_key
        self.client = client or httpx.Client(
            timeout=60.0, follow_redirects=True, trust_env=False
        )
        self.max_retries = max_retries

    def _request(self, params: dict[str, Any]) -> httpx.Response:
        headers = {"Accept": "application/json", "User-Agent": "bank-duration-risk-scanner/0.2"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        for attempt in range(self.max_retries):
            try:
                response = self.client.get(FDIC_BASE_URL, params=params, headers=headers)
            except httpx.TransportError:
                if attempt == self.max_retries - 1:
                    raise
                time.sleep(min(0.5 * (2**attempt), 8.0))
                continue
            if response.status_code not in {429, 500, 502, 503, 504}:
                response.raise_for_status()
                return response
            if attempt == self.max_retries - 1:
                response.raise_for_status()
            retry_after = response.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else 0.5 * (2**attempt)
            time.sleep(min(delay, 8.0))
        raise RuntimeError("FDIC request retries exhausted")

    def latest_report_date(self) -> date:
        response = self._request(
            {
                "fields": "REPDTE",
                "limit": 1,
                "sort_by": "REPDTE",
                "sort_order": "DESC",
                "format": "json",
            }
        )
        rows = response.json().get("data", [])
        if not rows:
            raise RuntimeError("FDIC API returned no reporting periods")
        return parse_date(rows[0]["data"]["REPDTE"])

    def fetch_period(self, report_date: date, *, use_cache: bool = True) -> list[CallReport]:
        stamp = report_date.strftime("%Y%m%d")
        # Schema-specific cache avoids silently reusing pre-liquidity downloads.
        cache_path = self.cache_dir / f"financials_{stamp}_v2.json"
        source_url = f"{FDIC_BASE_URL}?filters=REPDTE:{stamp}"
        if use_cache and cache_path.exists():
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            stamp_cached = payload.get("meta", {}).get("retrieved_at")
            retrieved_at = (datetime.fromisoformat(stamp_cached) if stamp_cached else
                            datetime.fromtimestamp(cache_path.stat().st_mtime, tz=UTC))
        else:
            offset = 0
            limit = 10_000
            collected: list[dict[str, Any]] = []
            while True:
                params = {
                    "filters": f"REPDTE:{stamp}",
                    "fields": ",".join(FDIC_FIELDS),
                    "limit": limit,
                    "offset": offset,
                    "sort_by": "CERT",
                    "sort_order": "ASC",
                    "format": "json",
                }
                page = self._request(params).json()
                rows = page.get("data", [])
                collected.extend(rows)
                total = int(page.get("meta", {}).get("total", len(collected)))
                if not rows and len(collected) < total:
                    raise RuntimeError("FDIC pagination ended before the reported total")
                if len(collected) >= total:
                    break
                offset += len(rows)
            payload = {"meta": {"source_url": source_url, "parameters": params,
                                 "fields": FDIC_FIELDS, "retrieved_at": datetime.now(UTC).isoformat()},
                       "data": collected}
            cache_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            retrieved_at = datetime.now(UTC)
        raw_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        payload_hash = hashlib.sha256(raw_bytes).hexdigest()
        reports: list[CallReport] = []
        for wrapper in payload.get("data", []):
            row = wrapper.get("data", wrapper)
            cert = row.get("CERT")
            if cert in (None, ""):
                continue
            htm_ac = _number(row.get("SCHA"))
            htm_reserve = _number(row.get("SCHTMRES")) or 0.0
            total_securities = _number(row.get("SC"))
            afs_fv = None
            if total_securities is not None and htm_ac is not None:
                # SC is balance-sheet securities: net HTM carrying value plus AFS fair value.
                afs_fv = total_securities - (htm_ac - htm_reserve)
                if afs_fv < -1e-6:
                    afs_fv = None
            reports.append(
                CallReport(
                    cert=int(cert),
                    report_date=parse_date(str(row["REPDTE"])),
                    source="fdic",
                    name=str(row.get("NAME") or f"FDIC Certificate {cert}"),
                    state=row.get("STALP"),
                    city=row.get("CITY"),
                    form_type=str(row["CALLFORM"]) if row.get("CALLFORM") is not None else None,
                    total_assets=_number(row.get("ASSET")),
                    total_deposits=_number(row.get("DEP")),
                    domestic_deposits=_number(row.get("DEPDOM")),
                    bank_equity=_number(row.get("EQ")),
                    preferred_equity=_number(row.get("EQPP")),
                    intangible_assets=_number(row.get("INTAN")),
                    htm_amortized_cost=htm_ac,
                    htm_fair_value=_number(row.get("SCHF")),
                    afs_amortized_cost=_number(row.get("SCAA")),
                    afs_fair_value=afs_fv,
                    uninsured_deposits=_number(row.get("DEPUNINS")),
                    deposit_interest_expense_ytd=_number(row.get("EDEPDOM")),
                    cash_and_balances=_number(row.get("CHBAL")),
                    other_borrowings=_number(row.get("OTHBOR")),
                    net_interest_margin=(float(row["NIMYQ"]) / 100.0 if _number(row.get("NIMYQ")) is not None else None),
                    source_url=source_url,
                    retrieved_at=retrieved_at,
                    payload_hash=payload_hash,
                    raw=row,
                )
            )
        return reports
