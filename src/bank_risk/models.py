from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    db_path: Path = Field(Path("data/bank_risk.sqlite"), alias="BANK_RISK_DB_PATH")
    cache_dir: Path = Field(Path("data/cache"), alias="BANK_RISK_CACHE_DIR")
    output_dir: Path = Field(Path("outputs"), alias="BANK_RISK_OUTPUT_DIR")
    config_path: Path = Field(Path("config/risk_model.yml"), alias="BANK_RISK_CONFIG_PATH")
    scenario_path: Path = Field(Path("config/stress_scenario.yml"), alias="BANK_RISK_SCENARIO_PATH")
    ffiec_user_id: str | None = Field(None, alias="FFIEC_USER_ID")
    ffiec_token: str | None = Field(None, alias="FFIEC_TOKEN")
    fdic_api_key: str | None = Field(None, alias="FDIC_API_KEY")


class CallReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cert: int
    report_date: date
    source: str
    name: str
    state: str | None = None
    city: str | None = None
    form_type: str | None = None
    total_assets: float | None = None
    total_deposits: float | None = None
    domestic_deposits: float | None = None
    bank_equity: float | None = None
    preferred_equity: float | None = None
    intangible_assets: float | None = None
    htm_amortized_cost: float | None = None
    htm_fair_value: float | None = None
    afs_amortized_cost: float | None = None
    afs_fair_value: float | None = None
    uninsured_deposits: float | None = None
    deposit_interest_expense_ytd: float | None = None
    cash_and_balances: float | None = None
    other_borrowings: float | None = None
    net_interest_margin: float | None = None
    source_url: str
    retrieved_at: datetime
    payload_hash: str
    raw: dict[str, Any] = Field(default_factory=dict)


class MarketRate(BaseModel):
    report_date: date
    avg_effr: float
    source: str = "FRED:DFF"
    source_url: str
    retrieved_at: datetime


@dataclass(frozen=True)
class RunResult:
    as_of: date
    quarters: int
    database: Path
    detailed_csv: Path
    top_risk_csv: Path
    workbook: Path
    bank_count: int
    stress_csv: Path
    manifest: Path
