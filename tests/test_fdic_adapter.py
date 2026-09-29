from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from bank_risk.adapters.fdic import FdicClient


def test_cached_fdic_payload_maps_securities_and_missing_values(tmp_path: Path) -> None:
    cache = tmp_path / "fdic"
    cache.mkdir(parents=True)
    payload = {
        "data": [
            {
                "data": {
                    "CERT": 24735,
                    "NAME": "SILICON VALLEY BANK",
                    "STALP": "CA",
                    "REPDTE": "20221231",
                    "ASSET": 209026000,
                    "DEPDOM": 161479000,
                    "EQ": 15456000,
                    "EQPP": 0,
                    "INTAN": 285000,
                    "SCHA": 91327000,
                    "SCHF": 76168000,
                    "SCAA": 28502000,
                    "SC": 117297000,
                    "SCHTMRES": 0,
                    "DEPUNINS": 151592000,
                    "EDEPDOM": 794000,
                    "CHBAL": 1000000,
                    "OTHBOR": 300000,
                    "NIMYQ": 2.5,
                }
            }
        ]
    }
    (cache / "financials_20221231_v2.json").write_text(json.dumps(payload), encoding="utf-8")
    report = FdicClient(tmp_path).fetch_period(__import__("datetime").date(2022, 12, 31))[0]
    assert report.afs_fair_value == 25_970_000
    assert report.htm_amortized_cost == 91_327_000
    assert report.uninsured_deposits == 151_592_000
    assert report.cash_and_balances == 1000000
    assert report.other_borrowings == 300000
    assert report.net_interest_margin == 0.025


class _SequenceClient:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = responses
        self.params: list[dict[str, Any]] = []

    def get(self, _url: str, *, params: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        self.params.append(params)
        return self.responses.pop(0)


def _response(status: int, payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        request=httpx.Request("GET", "https://api.fdic.gov/banks/financials"),
    )


def test_fdic_retries_paginates_and_preserves_schema_drift(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("bank_risk.adapters.fdic.time.sleep", lambda _seconds: None)
    first = {"data": {"CERT": 1, "NAME": "ONE", "REPDTE": "20221231", "NEW_FIELD": 7}}
    second = {"data": {"CERT": 2, "NAME": "TWO", "REPDTE": "20221231"}}
    client = _SequenceClient(
        [
            _response(503, {"error": "retry"}),
            _response(200, {"meta": {"total": 2}, "data": [first]}),
            _response(200, {"meta": {"total": 2}, "data": [second]}),
        ]
    )
    reports = FdicClient(tmp_path, client=client, max_retries=3).fetch_period(
        date(2022, 12, 31), use_cache=False
    )
    assert [report.cert for report in reports] == [1, 2]
    assert [params["offset"] for params in client.params] == [0, 0, 1]
    assert reports[0].raw["NEW_FIELD"] == 7
