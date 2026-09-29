from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from bank_risk.config import RiskModelConfig

METRIC_VERSION = "0.2.0"


def safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    valid = denominator.notna() & denominator.gt(0)
    result = pd.Series(np.nan, index=numerator.index, dtype="float64")
    result.loc[valid] = numerator.loc[valid] / denominator.loc[valid]
    return result.replace([np.inf, -np.inf], np.nan)


def _peer_bucket(assets: pd.Series) -> pd.Series:
    return pd.cut(
        assets.where(assets.gt(0)),
        bins=[0, 1_000_000, 10_000_000, 100_000_000, np.inf],
        labels=["<$1B", "$1–10B", "$10–100B", "≥$100B"],
        right=False,
    ).astype("string")


def _risk_tier(score: float | None, config: RiskModelConfig) -> str:
    if score is None or pd.isna(score):
        return "N/A"
    for tier in ("red", "amber", "watch"):
        if score >= config.risk_tiers[tier]:
            return tier.title()
    return "Low"


def _flags(row: pd.Series, config: RiskModelConfig) -> str:
    flags: list[str] = []
    if pd.notna(row["tce"]) and row["tce"] <= 0:
        flags.append("NONPOSITIVE_TCE")
    if pd.notna(row["adjusted_tce"]) and row["adjusted_tce"] <= 0:
        flags.append("NEGATIVE_ADJUSTED_TCE")
    for key, prefix, amber, red in (
        ("combined_loss_tce", "LOSS_TCE", "combined_loss_tce_amber", "combined_loss_tce_red"),
        ("uninsured_concentration", "UNINSURED", "uninsured_amber", "uninsured_red"),
        ("deposit_outflow_qoq", "DEPOSIT_OUTFLOW", "deposit_outflow_amber", "deposit_outflow_red"),
    ):
        value = row[key]
        if pd.notna(value):
            if value >= config.thresholds[red]:
                flags.append(f"{prefix}_RED")
            elif value >= config.thresholds[amber]:
                flags.append(f"{prefix}_AMBER")
    if (
        pd.notna(row["cash_uninsured_coverage"])
        and row["cash_uninsured_coverage"] < config.thresholds["cash_uninsured_amber"]
    ):
        flags.append("LOW_CASH_COVERAGE")
    if row["joint_loss_funding_warning"]:
        flags.append("JOINT_LOSS_UNINSURED")
    if row["balance_discontinuity"]:
        flags.append("LARGE_BALANCE_CHANGE")
    if row["negative_deposit_expense"]:
        flags.append("NEGATIVE_QUARTERLY_EXPENSE")
    if row["invalid_uninsured_deposits"]:
        flags.append("INVALID_UNINSURED_BALANCE")
    return " | ".join(flags)


def calculate_metrics(
    reports: pd.DataFrame,
    rates: pd.DataFrame,
    config: RiskModelConfig | None = None,
) -> pd.DataFrame:
    """Calculate v0.2 in order: balances, quarterly costs, funding pressure, peer score."""
    if reports.empty:
        return reports.copy()
    config = config or RiskModelConfig()
    frame = _prepare_reports(reports, rates)
    _add_balance_sheet_metrics(frame, config)
    _add_quarterly_deposit_cost(frame, config)
    _add_funding_pressure(frame, config)
    _add_peer_scores_and_flags(frame, config)
    return frame.drop(columns=["quarter_id", "_up", "_down"])


def _prepare_reports(reports: pd.DataFrame, rates: pd.DataFrame) -> pd.DataFrame:
    """Validate bank-quarter keys and attach the matching quarterly benchmark."""
    frame = reports.copy()
    frame["data_status"] = "Official-source filing"
    if "raw_json" in frame:
        demo = frame["raw_json"].str.contains(r'"demo"\s*:\s*true', regex=True, na=False)
        frame.loc[demo, "data_status"] = "Illustrative demo fixture"
    frame["report_date"] = pd.to_datetime(frame["report_date"]).dt.date
    rate_frame = rates.copy()
    rate_frame["report_date"] = pd.to_datetime(rate_frame["report_date"]).dt.date
    if frame.duplicated(["cert", "report_date"]).any():
        raise ValueError("Metrics require one source and one observation per bank-quarter")
    if rate_frame.duplicated("report_date").any():
        raise ValueError("Duplicate quarterly benchmark observations")
    if not pd.to_datetime(frame["report_date"]).dt.is_quarter_end.all():
        raise ValueError("Call Report dates must be calendar quarter ends")
    frame = frame.merge(
        rate_frame[["report_date", "avg_effr"]],
        on="report_date",
        how="left",
        validate="many_to_one",
    )
    frame = frame.sort_values(["cert", "report_date"]).reset_index(drop=True)
    for key in ("cash_and_balances", "other_borrowings", "net_interest_margin"):
        if key not in frame:
            frame[key] = np.nan
        frame[key] = pd.to_numeric(frame[key], errors="coerce")

    return frame


def _add_balance_sheet_metrics(frame: pd.DataFrame, config: RiskModelConfig) -> None:
    """Add loss absorption, uninsured funding and liquidity diagnostics."""
    # Missing equity deductions stay missing; no undocumented zero imputation.
    frame["tce"] = frame["bank_equity"] - frame["preferred_equity"] - frame["intangible_assets"]
    frame["htm_loss"] = (frame["htm_amortized_cost"] - frame["htm_fair_value"]).clip(lower=0)
    frame["afs_loss"] = (frame["afs_amortized_cost"] - frame["afs_fair_value"]).clip(lower=0)
    frame["combined_loss"] = frame[["htm_loss", "afs_loss"]].sum(axis=1, min_count=2)
    for kind in ("htm", "afs", "combined"):
        frame[f"{kind}_loss_tce"] = safe_divide(frame[f"{kind}_loss"], frame["tce"])
    frame["adjusted_tce"] = frame["tce"] - frame["htm_loss"]
    uninsured = frame["uninsured_deposits"]
    domestic = frame["domestic_deposits"]
    invalid_uninsured = uninsured.lt(0) | uninsured.gt(domestic)
    frame["invalid_uninsured_deposits"] = invalid_uninsured
    valid_uninsured = uninsured.where(~invalid_uninsured)
    frame["uninsured_concentration"] = safe_divide(valid_uninsured, domestic)
    frame["uninsured_assets"] = safe_divide(valid_uninsured, frame["total_assets"])
    frame["cash_uninsured_coverage"] = safe_divide(
        frame["cash_and_balances"].where(frame["cash_and_balances"].ge(0)), valid_uninsured
    )
    frame["borrowings_assets"] = safe_divide(
        frame["other_borrowings"].where(frame["other_borrowings"].ge(0)), frame["total_assets"]
    )
    frame["loss_funding_interaction"] = (
        frame["combined_loss_tce"] * frame["uninsured_concentration"]
    )
    frame["joint_loss_funding_warning"] = frame["combined_loss_tce"].ge(
        config.thresholds["combined_loss_tce_amber"]
    ) & frame["uninsured_concentration"].ge(config.thresholds["uninsured_amber"])


def _add_quarterly_deposit_cost(frame: pd.DataFrame, config: RiskModelConfig) -> None:
    """Convert YTD expense to quarterly cost, preserving gaps and balance-change checks."""
    domestic = frame["domestic_deposits"]
    frame["quarter_id"] = pd.to_datetime(frame["report_date"]).dt.to_period("Q").astype("int64")
    group = frame.groupby("cert", sort=False)
    consecutive = frame["quarter_id"].sub(group["quarter_id"].shift(1)).eq(1)
    frame["prior_report_date"] = group["report_date"].shift(1)
    frame["prior_domestic_deposits"] = group["domestic_deposits"].shift(1)
    frame["prior_deposit_expense_ytd"] = group["deposit_interest_expense_ytd"].shift(1)
    frame["deposit_growth_qoq"] = (
        safe_divide(domestic, frame["prior_domestic_deposits"]) - 1
    ).where(consecutive)
    frame["deposit_outflow_qoq"] = (-frame["deposit_growth_qoq"]).clip(lower=0)
    frame["asset_growth_qoq"] = (
        safe_divide(frame["total_assets"], group["total_assets"].shift(1)) - 1
    ).where(consecutive)
    frame["balance_discontinuity"] = frame["deposit_growth_qoq"].abs().gt(
        config.balance_jump_threshold
    ) | frame["asset_growth_qoq"].abs().gt(config.balance_jump_threshold)
    frame["borrowings_assets_change_qoq"] = (
        frame["borrowings_assets"] - group["borrowings_assets"].shift(1)
    ).where(consecutive)
    frame["nim_change_qoq"] = (
        frame["net_interest_margin"] - group["net_interest_margin"].shift(1)
    ).where(consecutive)

    q1 = pd.to_datetime(frame["report_date"]).dt.month.eq(3)
    expense = (frame["deposit_interest_expense_ytd"] - frame["prior_deposit_expense_ytd"]).where(
        consecutive
    )
    expense.loc[q1] = frame.loc[q1, "deposit_interest_expense_ytd"]
    frame["negative_deposit_expense"] = expense.lt(0)
    frame["deposit_interest_expense_quarter"] = expense.where(expense.ge(0))
    frame["average_domestic_deposits"] = ((domestic + frame["prior_domestic_deposits"]) / 2).where(
        consecutive
    )
    frame["deposit_cost"] = safe_divide(
        4 * frame["deposit_interest_expense_quarter"], frame["average_domestic_deposits"]
    ).where(~frame["balance_discontinuity"])
    frame["deposit_cost_status"] = np.select(
        [
            ~consecutive,
            frame["balance_discontinuity"],
            frame["negative_deposit_expense"],
            frame["deposit_cost"].isna(),
        ],
        [
            "Missing consecutive quarter",
            "Large balance change; review merger or flow",
            "Negative quarterly expense; review restatement",
            "Missing expense or positive deposit balance",
        ],
        default="Available",
    )


def _add_funding_pressure(frame: pd.DataFrame, config: RiskModelConfig) -> None:
    """Compare four-quarter deposit costs and label the benchmark-rate cycle."""
    group = frame.groupby("cert", sort=False)
    # Five consecutive intervals support two quarterly costs four quarters apart.
    continuous = frame["quarter_id"].sub(group["quarter_id"].shift(5)).eq(5)
    disruption = (
        group["balance_discontinuity"].transform(lambda s: s.rolling(5, min_periods=5).max()).eq(1)
    )
    frame["deposit_cost_4q_ago"] = group["deposit_cost"].shift(4).where(continuous)
    frame["effr_4q_ago"] = group["avg_effr"].shift(4).where(continuous)
    frame["effr_change_4q"] = frame["avg_effr"] - frame["effr_4q_ago"]
    rate_count = group["avg_effr"].transform(lambda s: s.rolling(5, min_periods=5).count())
    history_ok = continuous & ~disruption & rate_count.eq(5)
    frame["deposit_cost_change_4q"] = (frame["deposit_cost"] - frame["deposit_cost_4q_ago"]).where(
        history_ok
    )
    # Higher = more compression of EFFR-minus-deposit-cost, in either rate direction.
    frame["deposit_spread_compression_4q"] = (
        frame["deposit_cost_change_4q"] - frame["effr_change_4q"]
    )
    min_change = config.beta_min_rate_change_bps / 10_000
    meaningful = frame["effr_change_4q"].abs().ge(min_change)
    # Unlike positive-denominator ratios, beta must permit a negative rate change.
    frame["deposit_beta_4q"] = (
        frame["deposit_cost_change_4q"] / frame["effr_change_4q"].where(meaningful)
    ).replace([np.inf, -np.inf], np.nan)
    step = group["avg_effr"].diff()
    frame["_up"] = step.gt(0.0001)
    frame["_down"] = step.lt(-0.0001)
    up = frame.groupby("cert")["_up"].transform(lambda s: s.rolling(4, min_periods=4).max()).eq(1)
    down = (
        frame.groupby("cert")["_down"].transform(lambda s: s.rolling(4, min_periods=4).max()).eq(1)
    )
    frame["rate_regime"] = np.select(
        [~history_ok, up & down, ~meaningful, frame["effr_change_4q"].gt(0)],
        ["Insufficient history", "Mixed", "Flat / small change", "Rising"],
        default="Falling",
    )
    frame["beta_status"] = np.select(
        [~history_ok | frame["deposit_cost_change_4q"].isna(), ~meaningful, up & down],
        [
            "Insufficient comparable history",
            "EFFR change below threshold",
            "Mixed rate path; descriptive only",
        ],
        default="Descriptive only",
    )


def _add_peer_scores_and_flags(frame: pd.DataFrame, config: RiskModelConfig) -> None:
    """Score observed peer metrics; flag capital problems independently of ranks."""
    frame["asset_peer"] = _peer_bucket(frame["total_assets"])
    score_inputs = {
        "combined_loss_tce": "loss_percentile",
        "uninsured_concentration": "uninsured_percentile",
        "deposit_spread_compression_4q": "funding_percentile",
    }
    weighted = pd.Series(0.0, index=frame.index)
    available_weight = pd.Series(0.0, index=frame.index)
    for metric, percentile in score_inputs.items():
        peer = frame.groupby(["report_date", "asset_peer"], observed=True)[metric]
        frame[percentile] = peer.rank(pct=True, method="average", na_option="keep") * 100
        frame[f"{metric}_peer_count"] = peer.transform("count")
        present = frame[percentile].notna()
        weight = config.weights[metric]
        weighted.loc[present] += frame.loc[present, percentile] * weight
        available_weight.loc[present] += weight
    frame["score_coverage"] = available_weight
    full = pd.Series(np.isclose(available_weight, 1.0), index=frame.index) & frame[
        list(score_inputs.values())
    ].notna().all(axis=1)
    frame["partial_risk_score"] = weighted / available_weight.replace(0, np.nan)
    frame["risk_score"] = frame["partial_risk_score"].where(full)
    frame["score_status"] = np.where(full, "Full", "Partial")
    frame.loc[available_weight.eq(0), "score_status"] = "Unavailable"
    frame["risk_tier"] = [_risk_tier(v, config) for v in frame["partial_risk_score"]]
    # Critical capital flags affect review order without fabricating a percentile.
    frame["capital_alert"] = np.select(
        [frame["tce"].le(0), frame["adjusted_tce"].le(0)],
        ["Nonpositive TCE", "Nonpositive HTM-adjusted TCE"],
        default="",
    )
    frame["review_priority"] = frame["capital_alert"].ne("").astype(int)
    frame["risk_flags"] = frame.apply(_flags, axis=1, config=config)
    frame["metric_version"] = METRIC_VERSION


def latest_screen(metrics: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """One review order for both CSV and Excel: capital alerts, then complete scores."""
    latest = metrics.loc[pd.to_datetime(metrics["report_date"]).dt.date.eq(as_of)].copy()
    latest["display_score"] = latest["risk_score"].combine_first(latest["partial_risk_score"])
    latest["_full_score_sort"] = latest["score_status"].eq("Full").astype(int)
    return (
        latest.sort_values(
            ["review_priority", "_full_score_sort", "display_score", "combined_loss_tce"],
            ascending=False,
        )
        .drop(columns="_full_score_sort")
        .reset_index(drop=True)
    )
