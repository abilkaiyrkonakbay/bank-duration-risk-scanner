from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from bank_risk.stress import StressScenario, simulate_bank


def bank() -> dict[str, float]:
    return dict(total_assets=2000, domestic_deposits=1000, uninsured_deposits=800,
        cash_and_balances=100, afs_fair_value=200, htm_fair_value=300,
        htm_amortized_cost=400, tce=150, avg_effr=0.04, deposit_cost=0.02)


def scenario(**overrides: float) -> StressScenario:
    return StressScenario.model_validate({"cash_usable_fraction": 1, "afs_usable_fraction": 1,
        "afs_sale_discount": 0, "htm_sale_discount": 0, **overrides})


def test_cash_afs_then_gap_does_not_charge_existing_afs_mark_again() -> None:
    result = simulate_bank(bank(), scenario())
    assert result["stress_deposit_runoff"] == 330
    assert result["stress_cash_used"] == 100
    assert result["stress_afs_proceeds"] == 200
    assert result["stress_funding_gap"] == 30
    assert result["stress_tce_after_sales"] == 150
    assert result["stress_waterfall_residual"] == 0


def test_afs_additional_discount_and_htm_pro_rata_realization() -> None:
    result = simulate_bank(bank(), scenario(afs_sale_discount=0.10, htm_usable_fraction=1))
    assert result["stress_afs_proceeds"] == 180
    assert result["stress_afs_incremental_loss"] == pytest.approx(20)
    assert result["stress_htm_proceeds"] == 50
    assert result["stress_htm_realized_loss"] == pytest.approx(400 * 50 / 300 - 50)
    assert result["stress_tce_after_sales"] == pytest.approx(150 - 20 - (400 * 50 / 300 - 50))
    assert result["stress_funding_gap"] == 0


def test_borrowing_is_capped_and_premium_is_incremental_not_total_interest() -> None:
    result = simulate_bank(bank(), scenario(replacement_capacity_pct_assets=0.10))
    assert result["stress_replacement_borrowing"] == 200
    assert result["stress_afs_proceeds"] == 30
    assert result["stress_replacement_funding_premium"] == pytest.approx(200 * (0.06 - 0.02))
    assert result["stress_tce_after_funding_premium"] == pytest.approx(142)


@pytest.mark.parametrize("key", ["uninsured_deposits", "cash_and_balances", "afs_fair_value"])
def test_missing_active_inputs_are_not_zero(key: str) -> None:
    row = bank()
    row.pop(key)
    result = simulate_bank(row, scenario())
    assert result["stress_status"] == "Unavailable"
    assert result["stress_funding_gap"] is None
    assert key in result["stress_missing_inputs"]


def test_disabled_sources_do_not_require_balances() -> None:
    row = bank()
    for key in ("cash_and_balances", "afs_fair_value", "htm_fair_value", "htm_amortized_cost", "total_assets"):
        row.pop(key)
    result = simulate_bank(row, scenario(cash_usable_fraction=0, afs_usable_fraction=0))
    assert result["stress_funding_gap"] == 330


def test_missing_equity_or_cost_preserves_liquidity_output() -> None:
    row = bank()
    row.pop("tce")
    row.pop("deposit_cost")
    result = simulate_bank(row, scenario(replacement_capacity_pct_assets=0.1))
    assert result["stress_funding_gap"] == 0
    assert result["stress_tce_after_sales"] is None
    assert result["stress_replacement_funding_premium"] is None


@pytest.mark.parametrize("overrides", [{"afs_sale_discount": 1}, {"uninsured_runoff": -0.1}, {"horizon_months": 0}, {"replacement_spread_bps": math.inf}, {"typo": 0.5}])
def test_invalid_scenario_rejected(overrides: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        scenario(**overrides)


@pytest.mark.parametrize("runoff", [0, .2, .6, 1])
@pytest.mark.parametrize("htm_fraction", [0, .5, 1])
def test_waterfall_conservation_and_capacity_limits(runoff: float, htm_fraction: float) -> None:
    s = scenario(uninsured_runoff=runoff, insured_runoff=runoff, htm_usable_fraction=htm_fraction,
                 afs_sale_discount=.1, htm_sale_discount=.2, replacement_capacity_pct_assets=.05)
    result = simulate_bank(bank(), s)
    assert result["stress_waterfall_residual"] == pytest.approx(0, abs=1e-9)
    for used, capacity in [("cash_used", "cash_available"), ("replacement_borrowing", "borrowing_capacity"), ("afs_proceeds", "afs_capacity"), ("htm_proceeds", "htm_capacity")]:
        assert 0 <= result[f"stress_{used}"] <= result[f"stress_{capacity}"]
    assert result["stress_tce_after_sales"] <= bank()["tce"]


def test_invalid_uninsured_and_nonpositive_capital() -> None:
    row = bank()
    row["uninsured_deposits"] = 1001
    assert simulate_bank(row, scenario())["stress_status"] == "Unavailable"
    row["uninsured_deposits"] = 0
    row["tce"] = -1
    result = simulate_bank(row, scenario())
    assert result["stress_deposit_runoff"] == 50
    assert result["stress_equity_status"] == "Nonpositive"
