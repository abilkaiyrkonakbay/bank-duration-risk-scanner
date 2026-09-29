from datetime import date

import httpx
import pandas as pd
import pytest

from bank_risk.adapters.fred import FredClient


def test_full_quarter_daily_average_and_cached_repeat(tmp_path) -> None:  # type: ignore[no-untyped-def]
    dates = pd.date_range("2022-07-01", "2022-09-30")
    csv = "observation_date,DFF\n" + "\n".join(f"{d:%Y-%m-%d},{1 if d.month == 7 else 4}" for d in dates)
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, text=csv, request=req)))
    adapter = FredClient(tmp_path, client=client)
    rates = adapter.fetch_quarterly_effr(date(2022, 7, 1), date(2022, 9, 30))
    assert rates[0].avg_effr == pytest.approx((31 + 61 * 4) / 92 / 100)
    assert adapter.fetch_quarterly_effr(date(2022, 7, 1), date(2022, 9, 30))[0].avg_effr == rates[0].avg_effr


def test_partial_period_and_incomplete_daily_data_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    adapter = FredClient(tmp_path, client=httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, text="observation_date,DFF\n2022-09-01,3", request=req))))
    with pytest.raises(ValueError, match="complete calendar quarters"):
        adapter.fetch_quarterly_effr(date(2022, 9, 1), date(2022, 9, 30))
    with pytest.raises(ValueError, match="Incomplete FRED"):
        adapter.fetch_quarterly_effr(date(2022, 7, 1), date(2022, 9, 30))
