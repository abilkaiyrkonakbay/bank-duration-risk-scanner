from __future__ import annotations

import csv
import io
import time
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pandas as pd

from bank_risk.dates import quarter_end, quarter_sequence, quarter_start
from bank_risk.models import MarketRate

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


class FredClient:
    def __init__(
        self,
        cache_dir: Path,
        client: httpx.Client | None = None,
        max_retries: int = 4,
    ) -> None:
        self.cache_dir = cache_dir / "fred"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.client = client or httpx.Client(timeout=60.0, follow_redirects=True)
        self.max_retries = max_retries

    def fetch_quarterly_effr(self, start: date, end: date, *, use_cache: bool = True) -> list[MarketRate]:
        if start != quarter_start(start) or end != quarter_end(end) or start > end:
            raise ValueError("FRED averaging requires complete calendar quarters")
        cache_path = self.cache_dir / f"dff_{start:%Y%m%d}_{end:%Y%m%d}.csv"
        source_url = f"{FRED_CSV_URL}?id=DFF&cosd={start.isoformat()}&coed={end.isoformat()}"
        if use_cache and cache_path.exists():
            text = cache_path.read_text(encoding="utf-8")
            retrieved_at = datetime.fromtimestamp(cache_path.stat().st_mtime, tz=UTC)
        else:
            params = {"id": "DFF", "cosd": start.isoformat(), "coed": end.isoformat()}
            response: httpx.Response | None = None
            for attempt in range(self.max_retries):
                try:
                    response = self.client.get(
                        FRED_CSV_URL,
                        params=params,
                        headers={"Accept": "text/csv"},
                    )
                except httpx.TransportError:
                    if attempt == self.max_retries - 1:
                        raise
                    time.sleep(min(0.5 * (2**attempt), 8.0))
                    continue
                if response.status_code not in {429, 500, 502, 503, 504}:
                    response.raise_for_status()
                    break
                if attempt == self.max_retries - 1:
                    response.raise_for_status()
                time.sleep(0.5 * (2**attempt))
            if response is None:
                raise RuntimeError("FRED request failed")
            response.raise_for_status()
            text = response.text
            cache_path.write_text(text, encoding="utf-8")
            retrieved_at = datetime.now(UTC)
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows:
            raise RuntimeError("FRED DFF download returned no observations")
        date_key = "observation_date" if "observation_date" in rows[0] else "DATE"
        value_key = "DFF"
        frame = pd.DataFrame(rows)
        frame[date_key] = pd.to_datetime(frame[date_key], errors="coerce")
        frame[value_key] = pd.to_numeric(frame[value_key], errors="coerce") / 100.0
        frame = frame.dropna(subset=[date_key, value_key])
        frame = frame.loc[frame[date_key].dt.date.between(start, end)]
        if frame[date_key].duplicated().any():
            raise ValueError("Duplicate daily FRED observations")
        frame["report_date"] = frame[date_key].dt.date.map(quarter_end)
        counts = frame.groupby("report_date")[date_key].nunique()
        quarters = (end.year - start.year) * 4 + (end.month - start.month) // 3 + 1
        for period in quarter_sequence(end, quarters):
            expected_days = (period - quarter_start(period)).days + 1
            if counts.get(period, 0) != expected_days:
                raise ValueError(f"Incomplete FRED daily coverage for {period}; do not average a partial quarter")
        grouped = frame.groupby("report_date", as_index=False)[value_key].mean()
        return [
            MarketRate(
                report_date=row.report_date,
                avg_effr=float(row.DFF),
                source_url=source_url,
                retrieved_at=retrieved_at,
            )
            for row in grouped.itertuples(index=False)
        ]
