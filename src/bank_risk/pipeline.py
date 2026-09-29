from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from bank_risk.adapters.fdic import FdicClient
from bank_risk.adapters.fred import FredClient
from bank_risk.config import RiskModelConfig, load_risk_config
from bank_risk.dates import quarter_end, quarter_sequence, quarter_start
from bank_risk.db import Database
from bank_risk.excel import build_workbook
from bank_risk.metrics import METRIC_VERSION, calculate_metrics, latest_screen
from bank_risk.models import AppSettings, RunResult
from bank_risk.sample_data import demo_dataset
from bank_risk.stress import StressScenario, load_scenario, simulate_frame


def _write_outputs(
    metrics: pd.DataFrame,
    as_of: date,
    settings: AppSettings,
    config: RiskModelConfig,
    sources: pd.DataFrame,
    scenario: StressScenario,
    lineage: list[dict[str, object]],
) -> RunResult:
    output_dir = settings.output_dir / as_of.isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)
    latest = latest_screen(metrics, as_of)
    detailed_csv = output_dir / "bank_duration_risk_detailed.csv"
    top_csv = output_dir / "bank_duration_risk_top50.csv"
    stress_csv = output_dir / "bank_duration_risk_stress.csv"
    workbook = output_dir / "bank_duration_risk_scanner.xlsx"
    manifest = output_dir / "run_manifest.json"
    latest.to_csv(detailed_csv, index=False)
    latest.head(50).to_csv(top_csv, index=False)
    latest[["cert", "name", "report_date", *[c for c in latest if c.startswith("stress_")]]].to_csv(
        stress_csv, index=False
    )
    assumptions = {"risk_model": asdict(config), "stress_scenario": scenario.model_dump()}
    digest = hashlib.sha256(json.dumps(assumptions, sort_keys=True).encode()).hexdigest()
    manifest.write_text(
        json.dumps(
            {
                "model_version": METRIC_VERSION,
                "generated_at": datetime.now(UTC).isoformat(),
                "as_of": as_of.isoformat(),
                "display_quarters": metrics["report_date"].nunique(),
                "bank_count": len(latest),
                "configuration_sha256": digest,
                **assumptions,
                "sources": sources.to_dict("records"),
                "filings": lineage,
                "note": "Scenario assumptions are illustrative, not calibrated probabilities.",
            },
            default=str,
            indent=2,
        ),
        encoding="utf-8",
    )
    build_workbook(workbook, metrics, latest, as_of, config, sources, scenario=scenario)
    return RunResult(
        as_of=as_of,
        quarters=int(metrics["report_date"].nunique()),
        database=settings.db_path,
        detailed_csv=detailed_csv,
        top_risk_csv=top_csv,
        workbook=workbook,
        bank_count=len(latest),
        stress_csv=stress_csv,
        manifest=manifest,
    )


def _calculate_as_of(
    db: Database, as_of: date, config: RiskModelConfig, source: str, quarters: int = 8
) -> pd.DataFrame:
    if quarter_end(as_of) != as_of:
        raise ValueError("--as-of must be a calendar quarter end")
    reports = db.report_frame(source=source)
    rates = db.rate_frame()
    if reports.empty:
        raise ValueError(f"No {source} Call Reports found in {db.path}")
    first = quarter_sequence(as_of, quarters + 5)[0]
    reports = reports.loc[pd.to_datetime(reports["report_date"]).dt.date.between(first, as_of)]
    if not pd.to_datetime(reports["report_date"]).dt.date.eq(as_of).any():
        raise ValueError(f"No {source} filings for {as_of}")
    if rates.empty:
        raise ValueError("No market rates found; run scan or load FRED rates first")
    return calculate_metrics(reports, rates, config)


def calculate_and_export(
    db: Database,
    settings: AppSettings,
    as_of: date,
    source: str = "fdic",
    quarters: int = 8,
) -> RunResult:
    config = load_risk_config(settings.config_path)
    scenario = load_scenario(settings.scenario_path)
    metrics = _calculate_as_of(db, as_of, config, source, quarters)
    lineage = metrics[
        ["cert", "report_date", "source", "source_url", "retrieved_at", "payload_hash"]
    ].to_dict("records")
    first = quarter_sequence(as_of, quarters)[0]
    metrics = metrics.loc[pd.to_datetime(metrics["report_date"]).dt.date.ge(first)].copy()
    metrics = simulate_frame(metrics, scenario)
    db.replace_metrics(metrics, METRIC_VERSION)
    sources = db.source_manifest(
        as_of=as_of, source=source, first=quarter_sequence(as_of, quarters + 5)[0]
    )
    return _write_outputs(metrics, as_of, settings, config, sources, scenario, lineage)


def simulate(
    settings: AppSettings,
    as_of: date,
    scenario: StressScenario,
    cert: int | None = None,
    source: str = "fdic",
) -> Path:
    """Run an auditable scenario on stored data, without downloading or changing the baseline."""
    config = load_risk_config(settings.config_path)
    with Database(settings.db_path) as db:
        metrics = _calculate_as_of(db, as_of, config, source, quarters=1)
    metrics = metrics.loc[pd.to_datetime(metrics["report_date"]).dt.date.eq(as_of)]
    if cert is not None:
        metrics = metrics.loc[metrics["cert"].eq(cert)]
    if metrics.empty:
        raise ValueError("No bank matched the selected quarter and certificate")
    result = simulate_frame(metrics, scenario)
    output_dir = settings.output_dir / as_of.isoformat() / "scenarios"
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"_{cert}" if cert is not None else ""
    path = output_dir / f"{scenario.name}{suffix}.csv"
    result.to_csv(path, index=False)
    path.with_suffix(".json").write_text(
        json.dumps(
            {
                "model_version": METRIC_VERSION,
                "as_of": as_of.isoformat(),
                "scenario": scenario.model_dump(),
                "risk_model": asdict(config),
                "generated_at": datetime.now(UTC).isoformat(),
                "filings": result[
                    ["cert", "source", "source_url", "payload_hash", "retrieved_at"]
                ].to_dict("records"),
            },
            default=str,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def scan(
    settings: AppSettings,
    as_of: date | None = None,
    quarters: int = 8,
    source: str = "fdic",
    use_cache: bool = True,
) -> RunResult:
    if source != "fdic":
        raise ValueError(
            "The all-bank scan currently uses --source fdic; use FFIEC import/verify commands separately"
        )
    fdic = FdicClient(settings.cache_dir, api_key=settings.fdic_api_key)
    selected_date = as_of or fdic.latest_report_date()
    if quarter_end(selected_date) != selected_date:
        raise ValueError("--as-of must be a calendar quarter end")
    # Five warm-up quarters support both deposit-cost endpoints in the first display quarter.
    periods = quarter_sequence(selected_date, quarters + 5)
    with Database(settings.db_path) as db:
        run_id = db.start_run(selected_date, quarters, source)
        try:
            count = 0
            for period in periods:
                count += db.upsert_call_reports(fdic.fetch_period(period, use_cache=use_cache))
            rates = FredClient(settings.cache_dir).fetch_quarterly_effr(
                quarter_start(periods[0]), periods[-1], use_cache=use_cache
            )
            db.upsert_market_rates(rates)
            result = calculate_and_export(
                db, settings, selected_date, source=source, quarters=quarters
            )
            db.finish_run(run_id, "completed", count, f"Exported {result.bank_count} banks")
            return result
        except Exception as exc:
            db.finish_run(run_id, "failed", 0, str(exc))
            raise


def load_demo(settings: AppSettings) -> RunResult:
    reports, rates = demo_dataset()
    as_of = max(report.report_date for report in reports)
    with Database(settings.db_path) as db:
        run_id = db.start_run(as_of, 8, "fdic-demo")
        db.upsert_call_reports(reports)
        db.upsert_market_rates(rates)
        result = calculate_and_export(db, settings, as_of, source="fdic")
        db.finish_run(run_id, "completed", len(reports), "Deterministic demo dataset")
        return result
