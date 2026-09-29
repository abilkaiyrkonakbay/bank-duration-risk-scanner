from __future__ import annotations

from pathlib import Path

import pytest

from bank_risk.models import AppSettings


@pytest.fixture
def settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        BANK_RISK_DB_PATH=tmp_path / "bank_risk.sqlite",
        BANK_RISK_CACHE_DIR=tmp_path / "cache",
        BANK_RISK_OUTPUT_DIR=tmp_path / "outputs",
        BANK_RISK_CONFIG_PATH=Path("config/risk_model.yml"),
    )

