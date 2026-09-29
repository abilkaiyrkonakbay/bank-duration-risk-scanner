from __future__ import annotations

import pandas as pd
import pytest

from bank_risk.config import RiskModelConfig
from bank_risk.metrics import calculate_metrics
from bank_risk.sample_data import demo_dataset


def _frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    reports, rates = demo_dataset()
    report_frame = pd.DataFrame(
        [
            {
                **report.model_dump(exclude={"raw"}),
                "retrieved_at": report.retrieved_at.isoformat(),
            }
            for report in reports
        ]
    )
    rate_frame = pd.DataFrame([rate.model_dump() for rate in rates])
    return report_frame, rate_frame


def test_svb_2022q4_regression() -> None:
    reports, rates = _frames()
    metrics = calculate_metrics(reports, rates)
    svb = metrics.loc[(metrics["cert"] == 24735) & (metrics["report_date"].astype(str) == "2022-12-31")].iloc[0]
    assert svb["htm_loss"] == pytest.approx(15_159_000)
    assert svb["afs_loss"] == pytest.approx(2_532_000)
    assert svb["uninsured_concentration"] == pytest.approx(151_592_000 / 161_479_000)
    assert svb["combined_loss_tce"] > 1.0
    assert svb["adjusted_tce"] == pytest.approx((15_456_000 - 285_000) - 15_159_000)


def test_missing_uninsured_is_not_zero_and_score_is_partial() -> None:
    reports, rates = _frames()
    metrics = calculate_metrics(reports, rates)
    small = metrics.loc[(metrics["cert"] == 628) & (metrics["report_date"].astype(str) == "2022-12-31")].iloc[0]
    assert pd.isna(small["uninsured_concentration"])
    assert small["score_status"] == "Partial"
    assert small["score_coverage"] < 1.0


def test_negative_gains_are_floored_as_no_loss() -> None:
    reports, rates = _frames()
    first = reports.index[0]
    reports.loc[first, "htm_fair_value"] = reports.loc[first, "htm_amortized_cost"] + 1_000
    metrics = calculate_metrics(reports, rates)
    assert metrics.loc[first, "htm_loss"] == 0


def test_beta_suppressed_when_rate_change_is_too_small() -> None:
    reports, rates = _frames()
    rates["avg_effr"] = 0.02
    metrics = calculate_metrics(reports, rates, RiskModelConfig(beta_min_rate_change_bps=25))
    assert metrics["deposit_beta_4q"].isna().all()


def test_zero_tce_does_not_create_infinite_ratio() -> None:
    reports, rates = _frames()
    cert = int(reports.loc[0, "cert"])
    report_date = str(reports.loc[0, "report_date"])
    reports.loc[0, "bank_equity"] = reports.loc[0, "intangible_assets"]
    metrics = calculate_metrics(reports, rates)
    selected = metrics.loc[
        (metrics["cert"] == cert) & (metrics["report_date"].astype(str) == report_date)
    ].iloc[0]
    assert pd.isna(selected["combined_loss_tce"])


def test_negative_tce_does_not_create_a_negative_loss_ratio() -> None:
    reports, rates = _frames()
    cert = int(reports.loc[0, "cert"])
    report_date = str(reports.loc[0, "report_date"])
    reports.loc[0, "bank_equity"] = 0
    metrics = calculate_metrics(reports, rates)
    selected = metrics.loc[
        (metrics["cert"] == cert) & (metrics["report_date"].astype(str) == report_date)
    ].iloc[0]
    assert selected["tce"] < 0
    assert pd.isna(selected["combined_loss_tce"])


def test_q1_ytd_expense_is_a_quarterly_reset() -> None:
    reports, rates = _frames()
    metrics = calculate_metrics(reports, rates)
    q1 = metrics.loc[
        (metrics["cert"] == 99991)
        & (metrics["report_date"].astype(str) == "2022-03-31")
    ].iloc[0]
    assert q1["deposit_interest_expense_quarter"] == pytest.approx(
        q1["deposit_interest_expense_ytd"]
    )


def test_reporting_gap_suppresses_quarterly_cost_and_beta() -> None:
    reports, rates = _frames()
    gap_mask = (reports["cert"] == 99991) & (
        reports["report_date"].astype(str) == "2022-06-30"
    )
    metrics = calculate_metrics(reports.loc[~gap_mask], rates)
    after_gap = metrics.loc[
        (metrics["cert"] == 99991)
        & (metrics["report_date"].astype(str) == "2022-09-30")
    ].iloc[0]
    assert pd.isna(after_gap["deposit_cost"])
    assert pd.isna(after_gap["deposit_beta_4q"])


def test_rate_cuts_score_sticky_deposit_costs_as_more_pressure() -> None:
    reports, rates = _frames()
    reports = reports.loc[reports["cert"].eq(99991)].copy()
    reports["domestic_deposits"] = 1_000_000
    reports["total_assets"] = 2_000_000
    dates = sorted(reports["report_date"].unique())
    rates["avg_effr"] = [.06 - .005 * i for i in range(len(rates))]
    copies = []
    for cert, beta in [(1, .2), (2, .8)]:
        b = reports.copy()
        b["cert"] = cert
        ytd = 0.0
        for idx, period in enumerate(dates):
            if period.month == 3:
                ytd = 0
            ytd += (.04 - beta * .005 * idx) * 1_000_000 / 4
            b.loc[b["report_date"].eq(period), "deposit_interest_expense_ytd"] = ytd
        copies.append(b)
    metrics = calculate_metrics(pd.concat(copies), rates)
    last = metrics.loc[metrics["report_date"].eq(dates[-1])].set_index("cert")
    assert last.loc[1, "deposit_beta_4q"] == pytest.approx(.2)
    assert last.loc[2, "deposit_beta_4q"] == pytest.approx(.8)
    assert last.loc[1, "funding_percentile"] > last.loc[2, "funding_percentile"]
    assert last["rate_regime"].eq("Falling").all()


def test_flat_rates_still_allow_funding_compression_measure() -> None:
    reports, rates = _frames()
    rates["avg_effr"] = .02
    metrics = calculate_metrics(reports, rates)
    last = metrics.loc[metrics["report_date"].astype(str).eq("2022-12-31")]
    assert last["deposit_beta_4q"].isna().all()
    assert last["deposit_spread_compression_4q"].notna().all()
    assert last["rate_regime"].eq("Flat / small change").all()


def test_large_balance_change_suppresses_cost_and_future_comparisons() -> None:
    reports, rates = _frames()
    mask = reports["cert"].eq(99991) & reports["report_date"].astype(str).eq("2022-06-30")
    reports.loc[mask, "total_assets"] *= 2
    metrics = calculate_metrics(reports, rates)
    selected = metrics.loc[metrics["cert"].eq(99991)].tail(3)
    assert pd.isna(selected.iloc[0]["deposit_cost"])
    assert selected["deposit_spread_compression_4q"].isna().all()
    assert "LARGE_BALANCE_CHANGE" in selected.iloc[0]["risk_flags"]


def test_missing_equity_deduction_suppresses_tce_and_negative_capital_has_priority() -> None:
    reports, rates = _frames()
    reports.loc[reports["cert"].eq(99991), "preferred_equity"] = float("nan")
    reports.loc[reports["cert"].eq(628), "bank_equity"] = -1
    metrics = calculate_metrics(reports, rates)
    assert metrics.loc[metrics["cert"].eq(99991), "tce"].isna().all()
    assert metrics.loc[metrics["cert"].eq(628), "review_priority"].eq(1).all()


def test_negative_quarterly_expense_is_flagged_and_not_a_negative_cost() -> None:
    reports, rates = _frames()
    reports.loc[reports["report_date"].astype(str).eq("2022-06-30"), "deposit_interest_expense_ytd"] = 0
    metrics = calculate_metrics(reports, rates)
    q2 = metrics.loc[metrics["report_date"].astype(str).eq("2022-06-30")]
    assert q2["deposit_cost"].isna().all()
    assert q2["risk_flags"].str.contains("NEGATIVE_QUARTERLY_EXPENSE").all()


def test_duplicate_quarters_rejected() -> None:
    reports, rates = _frames()
    with pytest.raises(ValueError, match="one observation"):
        calculate_metrics(pd.concat([reports, reports.iloc[:1]]), rates)
