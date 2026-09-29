from __future__ import annotations

from datetime import date

import openpyxl
import pandas as pd

from bank_risk.db import Database
from bank_risk.pipeline import calculate_and_export, load_demo, scan, simulate
from bank_risk.stress import StressScenario


def test_demo_pipeline_creates_all_outputs(settings) -> None:  # type: ignore[no-untyped-def]
    result = load_demo(settings)
    assert result.database.exists()
    assert result.detailed_csv.exists()
    assert result.top_risk_csv.exists()
    assert result.workbook.exists()
    assert result.bank_count == 4

    workbook = openpyxl.load_workbook(result.workbook, data_only=False, read_only=False)
    assert workbook.sheetnames == [
        "Cover",
        "Screener",
        "Bank Detail",
        "Stress Test",
        "History",
        "Methodology",
        "Checks",
        "Sources",
    ]
    assert workbook["Screener"].freeze_panes == "H7"
    assert "BankScreener" in workbook["Screener"].tables
    assert len(workbook["Screener"].conditional_formatting) >= 3
    assert len(workbook["Bank Detail"]._charts) == 2
    assert "ModelAsOfDate" in workbook.defined_names
    assert "SelectedFDICCert" in workbook.defined_names
    assert str(workbook["Screener"]["J7"].value).startswith("=")
    assert workbook["Bank Detail"]["B4"].value == 24735
    formulas = [
        cell.value
        for sheet in workbook.worksheets
        for row in sheet.iter_rows()
        for cell in row
        if isinstance(cell.value, str) and cell.value.startswith("=")
    ]
    assert formulas
    assert not any("#REF!" in formula for formula in formulas)

    assert result.stress_csv is not None and result.stress_csv.exists()
    assert result.manifest is not None and result.manifest.exists()
    assert "Stress_uninsured_runoff" in workbook.defined_names
    assert len(workbook["Stress Test"].data_validations.dataValidation) == 10
    assert workbook["Stress Test"]["J15"].value == '=IF($J$4="Ready",J11/(1-D10)-J11,"N/A")'
    # Missing values stay explicit without creating formula errors or zero imputation.
    calculated = openpyxl.load_workbook(result.workbook, data_only=True, read_only=False)
    assert calculated["Cover"]["B6"].value.strftime("%Y-%m-%d") == "2022-12-31"
    assert calculated["Screener"]["O10"].value == "N/A"
    assert calculated["Stress Test"]["J22"].value == 0
    assert not any(cell.data_type == "e" for sheet in calculated for row in sheet for cell in row)


def test_export_excludes_future_data_and_respects_display_window(settings) -> None:  # type: ignore[no-untyped-def]
    load_demo(settings)
    with Database(settings.db_path) as db:
        result = calculate_and_export(db, settings, date(2022, 6, 30), quarters=2)
    history = pd.read_excel(result.workbook, sheet_name="History")
    assert sorted(history["Report date"].dt.date.unique()) == [date(2022, 3, 31), date(2022, 6, 30)]
    assert pd.read_csv(result.detailed_csv)["report_date"].eq("2022-06-30").all()


def test_scan_fetches_warmup_and_whole_first_rate_quarter(settings, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    periods = []
    rate_windows = []
    monkeypatch.setattr("bank_risk.pipeline.FdicClient.fetch_period", lambda self, period, **kwargs: periods.append(period) or [])
    monkeypatch.setattr("bank_risk.pipeline.FredClient.fetch_quarterly_effr", lambda self, start, end, **kwargs: rate_windows.append((start, end)) or [])
    # Capture download boundaries without requiring a workbook for the empty mocked universe.
    sentinel = type("Result", (), {"bank_count": 1})()
    monkeypatch.setattr("bank_risk.pipeline.calculate_and_export", lambda *args, **kwargs: sentinel)
    scan(settings, date(2026, 6, 30), 8)
    assert len(periods) == 13
    assert periods[0] == date(2023, 6, 30)
    assert rate_windows == [(date(2023, 4, 1), date(2026, 6, 30))]


def test_simulate_does_not_change_baseline_outputs(settings) -> None:  # type: ignore[no-untyped-def]
    result = load_demo(settings)
    baseline = result.detailed_csv.read_bytes()
    path = simulate(settings, date(2022, 12, 31), StressScenario(), cert=24735)
    assert len(pd.read_csv(path)) == 1
    assert path.with_suffix(".json").exists()
    assert result.detailed_csv.read_bytes() == baseline


def test_export_runs_model_once_and_shares_csv_excel_order(settings, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from unittest.mock import Mock

    from bank_risk import pipeline

    metrics_spy = Mock(wraps=pipeline.calculate_metrics)
    stress_spy = Mock(wraps=pipeline.simulate_frame)
    monkeypatch.setattr(pipeline, "calculate_metrics", metrics_spy)
    monkeypatch.setattr(pipeline, "simulate_frame", stress_spy)
    result = load_demo(settings)
    metrics_spy.assert_called_once()
    stress_spy.assert_called_once()
    detailed = pd.read_csv(result.detailed_csv)
    screener = pd.read_excel(result.workbook, sheet_name="Screener", header=5)
    assert screener["FDIC cert"].tolist() == detailed["cert"].tolist()
    assert screener["Score status"].tolist() == detailed["score_status"].tolist()
    assert screener["Score"].tolist() == detailed["display_score"].tolist()
