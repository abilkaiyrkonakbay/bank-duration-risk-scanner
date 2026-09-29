"""Deterministic deposit-run waterfall. All balances remain in reported $000."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from bank_risk.config import _simple_yaml


class StressScenario(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    name: str = Field(default="funding_stress", pattern=r"^[A-Za-z0-9_-]{1,40}$")
    uninsured_runoff: float = Field(default=0.40, ge=0, le=1)
    insured_runoff: float = Field(default=0.05, ge=0, le=1)
    cash_usable_fraction: float = Field(default=0.80, ge=0, le=1)
    afs_usable_fraction: float = Field(default=0.75, ge=0, le=1)
    htm_usable_fraction: float = Field(default=0.0, ge=0, le=1)
    afs_sale_discount: float = Field(default=0.03, ge=0, lt=1)
    htm_sale_discount: float = Field(default=0.05, ge=0, lt=1)
    replacement_capacity_pct_assets: float = Field(default=0.0, ge=0, le=1)
    replacement_spread_bps: float = Field(default=200, ge=0, le=5000)
    horizon_months: float = Field(default=12, gt=0, le=60)


def load_scenario(path: Path | None = None) -> StressScenario:
    if path is None:
        return StressScenario()
    if not path.exists():
        raise FileNotFoundError(f"Scenario file not found: {path}")
    return StressScenario.model_validate(_simple_yaml(path))


def _value(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value is None or pd.isna(value):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def simulate_bank(row: dict[str, Any], scenario: StressScenario) -> dict[str, Any]:
    """Cash -> assumed borrowing -> AFS -> permitted HTM -> uncovered gap.

    AFS's existing mark is already in equity: only an additional sale discount is
    charged. HTM's amortized-cost-to-proceeds shortfall is charged only on sales.
    TCE after sales is a pre-tax proxy, not a full solvency or earnings forecast.
    """
    result: dict[str, Any] = {"scenario": scenario.name}
    numeric_keys = (
        "deposit_runoff",
        "cash_available",
        "cash_used",
        "borrowing_capacity",
        "replacement_borrowing",
        "afs_capacity",
        "afs_proceeds",
        "htm_capacity",
        "htm_proceeds",
        "funding_gap",
        "afs_incremental_loss",
        "htm_realized_loss",
        "incremental_sale_loss",
        "tce_after_sales",
        "replacement_rate",
        "replacement_funding_premium",
        "tce_after_funding_premium",
        "waterfall_residual",
    )
    result.update(dict.fromkeys(numeric_keys))
    required = ["domestic_deposits", "uninsured_deposits"]
    if scenario.cash_usable_fraction > 0:
        required.append("cash_and_balances")
    if scenario.afs_usable_fraction > 0:
        required.append("afs_fair_value")
    if scenario.htm_usable_fraction > 0:
        required.extend(["htm_fair_value", "htm_amortized_cost"])
    if scenario.replacement_capacity_pct_assets > 0:
        required.append("total_assets")
    values = {key: _value(row, key) for key in required}
    invalid = [key for key, value in values.items() if value is None or value < 0]
    deposits = values["domestic_deposits"]
    uninsured = values["uninsured_deposits"]
    if deposits is not None and deposits <= 0:
        invalid.append("positive domestic_deposits")
    if deposits is not None and uninsured is not None and uninsured > deposits:
        invalid.append("uninsured_deposits exceeds domestic_deposits")
    if invalid:
        result.update(
            status="Unavailable",
            missing_inputs="; ".join(invalid),
            equity_status="Unavailable",
            funding_premium_status="Unavailable",
        )
        return {f"stress_{key}": value for key, value in result.items()}
    assert deposits is not None and uninsured is not None
    runoff = (
        uninsured * scenario.uninsured_runoff + (deposits - uninsured) * scenario.insured_runoff
    )
    cash_available = (values.get("cash_and_balances") or 0) * scenario.cash_usable_fraction
    cash_used = min(runoff, cash_available)
    capacity = (values.get("total_assets") or 0) * scenario.replacement_capacity_pct_assets
    borrowing = min(max(runoff - cash_used, 0), capacity)
    afs_fv = values.get("afs_fair_value") or 0
    afs_capacity = afs_fv * scenario.afs_usable_fraction * (1 - scenario.afs_sale_discount)
    afs_proceeds = min(max(runoff - cash_used - borrowing, 0), afs_capacity)
    afs_sold_fv = afs_proceeds / (1 - scenario.afs_sale_discount)
    afs_loss = afs_sold_fv - afs_proceeds
    htm_fv = values.get("htm_fair_value") or 0
    htm_capacity = htm_fv * scenario.htm_usable_fraction * (1 - scenario.htm_sale_discount)
    remaining = max(runoff - cash_used - borrowing - afs_proceeds, 0)
    htm_proceeds = min(remaining, htm_capacity)
    htm_sold_fv = htm_proceeds / (1 - scenario.htm_sale_discount)
    htm_book_sold = (
        (values.get("htm_amortized_cost") or 0) * htm_sold_fv / htm_fv if htm_fv > 0 else 0
    )
    htm_loss = max(htm_book_sold - htm_proceeds, 0)
    gap = max(remaining - htm_proceeds, 0)
    sale_loss = afs_loss + htm_loss
    tce = _value(row, "tce")
    after_sales = None if tce is None else tce - sale_loss
    effr = _value(row, "avg_effr")
    deposit_cost = _value(row, "deposit_cost")
    replacement_rate = (
        None if effr is None else max(effr + scenario.replacement_spread_bps / 10_000, 0)
    )
    premium = None
    if borrowing == 0:
        premium = 0.0
    elif replacement_rate is not None and deposit_cost is not None:
        premium = borrowing * (replacement_rate - deposit_cost) * scenario.horizon_months / 12
    result.update(
        status="Funding gap" if gap > 1e-8 else "Covered under assumptions",
        missing_inputs="",
        deposit_runoff=runoff,
        cash_available=cash_available,
        cash_used=cash_used,
        borrowing_capacity=capacity,
        replacement_borrowing=borrowing,
        afs_capacity=afs_capacity,
        afs_proceeds=afs_proceeds,
        htm_capacity=htm_capacity,
        htm_proceeds=htm_proceeds,
        funding_gap=gap,
        afs_incremental_loss=afs_loss,
        htm_realized_loss=htm_loss,
        incremental_sale_loss=sale_loss,
        tce_after_sales=after_sales,
        equity_status="Missing TCE"
        if after_sales is None
        else "Nonpositive"
        if after_sales <= 0
        else "Positive",
        replacement_rate=replacement_rate,
        replacement_funding_premium=premium,
        funding_premium_status="Missing EFFR or deposit cost" if premium is None else "Available",
        tce_after_funding_premium=None
        if after_sales is None or premium is None
        else after_sales - premium,
        waterfall_residual=runoff - cash_used - borrowing - afs_proceeds - htm_proceeds - gap,
    )
    return {f"stress_{key}": value for key, value in result.items()}


def simulate_frame(frame: pd.DataFrame, scenario: StressScenario | None = None) -> pd.DataFrame:
    scenario = scenario or StressScenario()
    if frame.empty:
        return frame.copy()
    results = pd.DataFrame(
        [simulate_bank(row, scenario) for row in frame.to_dict("records")], index=frame.index
    )
    base = frame.drop(columns=[key for key in frame if key.startswith("stress_")])
    return pd.concat([base, results], axis=1)
