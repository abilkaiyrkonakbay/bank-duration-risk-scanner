from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from bank_risk.adapters.fdic import FdicClient
from bank_risk.adapters.fred import FredClient

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("RUN_LIVE_TESTS") != "1", reason="set RUN_LIVE_TESTS=1"),
]


def test_live_fdic_latest_period(tmp_path: Path) -> None:
    assert FdicClient(tmp_path).latest_report_date() >= date(2022, 12, 31)


def test_live_fred_quarterly_rate(tmp_path: Path) -> None:
    rates = FredClient(tmp_path).fetch_quarterly_effr(
        date(2025, 1, 1), date(2025, 3, 31), use_cache=False
    )
    assert rates and rates[0].avg_effr > 0
