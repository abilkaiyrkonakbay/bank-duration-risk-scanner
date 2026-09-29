from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RiskModelConfig:
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "combined_loss_tce": 0.50,
            "uninsured_concentration": 0.30,
            "deposit_spread_compression_4q": 0.20,
        }
    )
    beta_min_rate_change_bps: float = 25.0
    balance_jump_threshold: float = 0.30
    thresholds: dict[str, float] = field(
        default_factory=lambda: {
            "combined_loss_tce_amber": 0.50,
            "combined_loss_tce_red": 1.00,
            "uninsured_amber": 0.40,
            "uninsured_red": 0.60,
            "deposit_outflow_amber": 0.05,
            "deposit_outflow_red": 0.10,
            "cash_uninsured_amber": 0.25,
        }
    )
    risk_tiers: dict[str, float] = field(
        default_factory=lambda: {"red": 80.0, "amber": 60.0, "watch": 40.0}
    )

    def __post_init__(self) -> None:
        expected = {"combined_loss_tce", "uninsured_concentration", "deposit_spread_compression_4q"}
        if set(self.weights) != expected:
            raise ValueError(
                "v0.2 weights must use combined_loss_tce, uninsured_concentration and deposit_spread_compression_4q (raw beta is diagnostic only)"
            )
        if (
            any(not isfinite(v) or v < 0 for v in self.weights.values())
            or abs(sum(self.weights.values()) - 1) > 1e-9
        ):
            raise ValueError("Risk-model weights must be finite, nonnegative and sum to 1.0")
        if not isfinite(self.beta_min_rate_change_bps) or self.beta_min_rate_change_bps <= 0:
            raise ValueError("beta_min_rate_change_bps must be positive and finite")
        if not isfinite(self.balance_jump_threshold) or self.balance_jump_threshold <= 0:
            raise ValueError("balance_jump_threshold must be positive and finite")
        if not all(isfinite(v) and v >= 0 for v in self.thresholds.values()):
            raise ValueError("Warning thresholds must be nonnegative and finite")
        if (
            not 0
            <= self.risk_tiers["watch"]
            < self.risk_tiers["amber"]
            < self.risk_tiers["red"]
            <= 100
        ):
            raise ValueError("Risk tiers must satisfy 0 <= watch < amber < red <= 100")


def _simple_yaml(path: Path) -> dict[str, Any]:
    """Parse the repository's intentionally small YAML subset without another dependency."""
    result: dict[str, Any] = {}
    section: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith(" ") and line.endswith(":"):
            section = line[:-1].strip()
            result[section] = {}
            continue
        key, value = [part.strip() for part in line.split(":", 1)]
        parsed: Any
        if value.lower() in {"true", "false"}:
            parsed = value.lower() == "true"
        else:
            try:
                parsed = float(value)
            except ValueError:
                parsed = value.strip("\"'")
        if section and line.startswith(" "):
            result[section][key] = parsed
        else:
            result[key] = parsed
            section = None
    return result


def load_risk_config(path: Path | None = None) -> RiskModelConfig:
    if path is None or not path.exists():
        return RiskModelConfig()
    raw = _simple_yaml(path)
    defaults = RiskModelConfig()
    return RiskModelConfig(
        weights=raw.get("weights", defaults.weights),
        beta_min_rate_change_bps=float(raw.get("beta_min_rate_change_bps", 25.0)),
        balance_jump_threshold=float(raw.get("balance_jump_threshold", 0.30)),
        thresholds={**defaults.thresholds, **raw.get("thresholds", {})},
        risk_tiers={**defaults.risk_tiers, **raw.get("risk_tiers", {})},
    )
