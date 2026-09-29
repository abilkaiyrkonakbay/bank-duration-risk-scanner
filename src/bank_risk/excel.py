from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import xlsxwriter

from bank_risk.config import RiskModelConfig
from bank_risk.excel_stress import write_stress
from bank_risk.stress import StressScenario

FDIC_URL = "https://api.fdic.gov/banks/docs/"
FFIEC_URL = "https://cdr.ffiec.gov/public/PWS/PWSPage.aspx"
FFIEC_FORM_URL = (
    "https://www.ffiec.gov/sites/default/files/data/reporting-forms/FFIEC031_202606_f.pdf"
)
FRED_URL = "https://fred.stlouisfed.org/series/DFF"


def _excel_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return "N/A"
    if hasattr(value, "item"):
        return value.item()
    return value


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value == "") or bool(pd.isna(value))


def _write_value(
    sheet: xlsxwriter.worksheet.Worksheet, row: int, col: int,
    value: Any, cell_format: xlsxwriter.format.Format,
) -> None:
    """Write reported scalars safely, keeping missing values distinct from zero."""
    if _is_missing(value):
        value = "N/A"
    if isinstance(value, str):
        sheet.write_string(row, col, value, cell_format)
    else:
        sheet.write_number(row, col, float(value), cell_format)


def _formats(workbook: xlsxwriter.Workbook) -> dict[str, xlsxwriter.format.Format]:
    navy = "#17365D"
    blue = "#1F4E78"
    light_blue = "#D9EAF7"
    green = "#E2F0D9"
    amber = "#FFF2CC"
    red = "#F4CCCC"
    gray = "#E7E6E6"
    return {
        "title": workbook.add_format(
            {"font_name": "Aptos Display", "font_size": 22, "bold": True, "font_color": navy}
        ),
        "subtitle": workbook.add_format(
            {"font_name": "Aptos", "font_size": 10, "font_color": "#666666"}
        ),
        "section": workbook.add_format(
            {"bold": True, "font_color": "#FFFFFF", "bg_color": navy, "align": "left"}
        ),
        "header": workbook.add_format(
            {
                "bold": True,
                "font_color": "#FFFFFF",
                "bg_color": blue,
                "border": 0,
                "bottom": 1,
                "bottom_color": "#FFFFFF",
                "text_wrap": True,
                "valign": "vcenter",
                "align": "center",
            }
        ),
        "raw_header": workbook.add_format(
            {
                "bold": True,
                "font_color": "#333333",
                "bg_color": gray,
                "text_wrap": True,
                "valign": "vcenter",
                "align": "center",
            }
        ),
        "label": workbook.add_format({"bold": True, "font_color": "#444444"}),
        "input": workbook.add_format(
            {"font_color": "#0000FF", "bg_color": "#FFF2CC", "border": 1, "num_format": "0"}
        ),
        "formula": workbook.add_format({"font_color": "#000000"}),
        "link": workbook.add_format({"font_color": "#008000", "underline": True}),
        "external": workbook.add_format({"font_color": "#FF0000", "underline": True}),
        "money1": workbook.add_format(
            {"num_format": "$#,##0.0;[Red]($#,##0.0);-", "align": "right"}
        ),
        "number": workbook.add_format({"num_format": "#,##0;[Red](#,##0);-", "align": "right"}),
        "score": workbook.add_format({"num_format": "0.0", "align": "right"}),
        "percent": workbook.add_format({"num_format": "0.0%;[Red](0.0%);-", "align": "right"}),
        "date": workbook.add_format({"num_format": "yyyy-mm-dd", "align": "right"}),
        "body": workbook.add_format({"font_name": "Aptos", "font_size": 9}),
        "note": workbook.add_format(
            {"font_name": "Aptos", "font_size": 9, "font_color": "#666666", "text_wrap": True}
        ),
        "kpi": workbook.add_format(
            {"font_size": 16, "bold": True, "font_color": navy, "bg_color": light_blue, "align": "center"}
        ),
        "kpi_date": workbook.add_format(
            {
                "font_size": 16,
                "bold": True,
                "font_color": navy,
                "bg_color": light_blue,
                "align": "center",
                "num_format": "yyyy-mm-dd",
            }
        ),
        "kpi_label": workbook.add_format(
            {"font_size": 9, "bold": True, "font_color": "#555555", "bg_color": light_blue, "align": "center"}
        ),
        "ok": workbook.add_format({"bg_color": green, "font_color": "#006100", "bold": True}),
        "warn": workbook.add_format({"bg_color": amber, "font_color": "#9C6500", "bold": True}),
        "fail": workbook.add_format({"bg_color": red, "font_color": "#9C0006", "bold": True}),
    }


def build_workbook(
    path: Path,
    metrics: pd.DataFrame,
    latest: pd.DataFrame,
    as_of: date,
    config: RiskModelConfig,
    sources: pd.DataFrame | None = None,
    scenario: StressScenario | None = None,
) -> None:
    """Present prepared metrics and review order without running the model again."""
    path.parent.mkdir(parents=True, exist_ok=True)
    scenario = scenario or StressScenario()
    frame = metrics.copy()
    frame["report_date"] = pd.to_datetime(frame["report_date"]).dt.date
    latest = latest.copy()
    latest["risk_rank"] = range(1, len(latest) + 1)
    workbook = xlsxwriter.Workbook(path, {"nan_inf_to_errors": True, "constant_memory": False})
    workbook.set_properties(
        {
            "title": "Bank Duration-Risk Scanner",
            "subject": "SVB-style Call Report risk screen",
            "author": "Bank Duration-Risk Scanner",
            "comments": "Educational analysis; not investment advice.",
        }
    )
    fmt = _formats(workbook)

    cover = workbook.add_worksheet("Cover")
    screener = workbook.add_worksheet("Screener")
    detail = workbook.add_worksheet("Bank Detail")
    stress = workbook.add_worksheet("Stress Test")
    history = workbook.add_worksheet("History")
    methodology = workbook.add_worksheet("Methodology")
    checks = workbook.add_worksheet("Checks")
    source_sheet = workbook.add_worksheet("Sources")
    workbook.define_name("ModelAsOfDate", "='Cover'!$B$6")
    workbook.define_name("SelectedFDICCert", "='Bank Detail'!$B$4")
    for sheet in (cover, screener, detail, history, methodology, checks, source_sheet):
        sheet.hide_gridlines(2)

    _write_screener(workbook, screener, latest, fmt)
    _write_history(history, frame, fmt)
    default_cert = int(latest.iloc[0]["cert"]) if not latest.empty else 0
    _write_detail(workbook, detail, frame, latest, default_cert, fmt)
    write_stress(workbook, stress, latest.iloc[0].to_dict(), len(latest), scenario, fmt)
    _write_cover(workbook, cover, latest, as_of, fmt)
    _write_methodology(methodology, config, fmt)
    _write_checks(checks, latest, frame, fmt)
    _write_sources(source_sheet, sources, fmt)
    if latest["data_status"].eq("Illustrative demo fixture").any():
        cover.merge_range("B8:M8", "DEMO — synthetic trends, liquidity balances and peers. Only stated SVB Q4 regression targets are anchored; not a historical backtest.", fmt["warn"])
        stress.merge_range("A38:K39", "DEMO INPUTS: cash, borrowing balances and NIM are illustrative, including for SVB. Scenario outcomes are not a reconstruction of March 2023.", fmt["note"])
    workbook.close()


def _write_cover(
    workbook: xlsxwriter.Workbook,
    sheet: xlsxwriter.worksheet.Worksheet,
    latest: pd.DataFrame,
    as_of: date,
    fmt: dict[str, xlsxwriter.format.Format],
) -> None:
    sheet.set_column("A:A", 3)
    sheet.set_column("B:H", 16)
    sheet.set_column("I:I", 3)
    sheet.set_column("J:M", 16)
    sheet.set_row(1, 34)
    sheet.set_row(2, 18)
    sheet.merge_range("B2:M2", "Bank Duration-Risk Scanner", fmt["title"])
    sheet.merge_range(
        "B3:M3",
        "Screens US Call Report filers for securities-loss absorption, funding concentration, and deposit repricing.",
        fmt["subtitle"],
    )
    sheet.write("B5", "As of", fmt["kpi_label"])
    sheet.write_datetime("B6", as_of, fmt["kpi_date"])
    sheet.write("D5", "Banks screened", fmt["kpi_label"])
    sheet.write_number("D6", len(latest), fmt["kpi"])
    full_count = int(latest["score_status"].eq("Full").sum()) if not latest.empty else 0
    sheet.write("F5", "Full scores", fmt["kpi_label"])
    sheet.write_number("F6", full_count, fmt["kpi"])
    red_count = int(latest["risk_tier"].eq("Red").sum()) if not latest.empty else 0
    sheet.write("H5", "Red tier", fmt["kpi_label"])
    sheet.write_number("H6", red_count, fmt["kpi"])
    sheet.merge_range("B9:H9", "How to use this workbook", fmt["section"])
    instructions = [
        "1. Start in Screener and filter by risk tier, asset peer, state, or absolute warning flags.",
        "2. Open Bank Detail and enter an FDIC certificate number in the yellow cell to inspect eight-quarter trends.",
        "3. Edit the blue assumptions on Stress Test to explore runoff, liquidity capacity and incremental equity losses.",
        "4. Use Checks and Sources to audit completeness, formulas, definitions, and provenance.",
    ]
    for row, text in enumerate(instructions, start=10):
        sheet.merge_range(row, 1, row, 7, text, fmt["note"])
        sheet.set_row(row, 24)
    sheet.merge_range("B16:H16", "Accounting caution", fmt["section"])
    sheet.merge_range(
        "B17:H19",
        "AFS marks are already reflected through AOCI in GAAP equity. Adjusted TCE therefore subtracts only hidden HTM losses; combined loss/TCE is an exposure ratio, not a second equity deduction.",
        fmt["note"],
    )
    sheet.merge_range("B21:H21", "Formatting legend", fmt["section"])
    sheet.write("B22", "Blue font", fmt["input"])
    sheet.write("C22", "Editable input", fmt["note"])
    sheet.write("D22", "Green font", fmt["link"])
    sheet.write("E22", "Internal link/formula", fmt["note"])
    sheet.write("F22", "Red font", fmt["external"])
    sheet.write("G22", "External source", fmt["note"])

    # Formula-backed helper range for one compact, decision-useful chart.
    chart_rows = latest.dropna(subset=["combined_loss_tce", "uninsured_concentration"]).head(3)
    helper_count = len(chart_rows)
    sheet.write_row("J9", ["Bank", "Combined loss/TCE", "Uninsured concentration"], fmt["raw_header"])
    for chart_position, (source_index, data_row) in enumerate(chart_rows.iterrows()):
        source_row = 7 + int(source_index)
        out_row = 10 + chart_position
        sheet.write_formula(out_row - 1, 9, f"='Screener'!G{source_row}", fmt["formula"], data_row["name"])
        sheet.write_formula(
            out_row - 1,
            10,
            f"='Screener'!M{source_row}",
            fmt["percent"],
            _excel_value(data_row["combined_loss_tce"]),
        )
        sheet.write_formula(
            out_row - 1,
            11,
            f"='Screener'!O{source_row}",
            fmt["percent"],
            _excel_value(data_row["uninsured_concentration"]),
        )
    if helper_count:
        chart = workbook.add_chart({"type": "bar", "subtype": "clustered"})
        chart.add_series(
            {
                "name": "Combined loss / TCE",
                "categories": f"='Cover'!$J$10:$J${9 + helper_count}",
                "values": f"='Cover'!$K$10:$K${9 + helper_count}",
                "fill": {"color": "#C00000"},
            }
        )
        chart.add_series(
            {
                "name": "Uninsured concentration",
                "categories": f"='Cover'!$J$10:$J${9 + helper_count}",
                "values": f"='Cover'!$L$10:$L${9 + helper_count}",
                "fill": {"color": "#5B9BD5"},
            }
        )
        chart.set_title({"name": "Top-risk banks: exposure comparison"})
        chart.set_x_axis({"name": "% of TCE or domestic deposits", "num_format": "0%", "major_gridlines": {"visible": True, "line": {"color": "#E7E6E6"}}})
        chart.set_legend({"position": "bottom"})
        chart.set_style(10)
        sheet.insert_chart("J2", chart, {"x_scale": 1.15, "y_scale": 1.1})
    sheet.set_tab_color("#17365D")


def _write_screener(
    workbook: xlsxwriter.Workbook,
    sheet: xlsxwriter.worksheet.Worksheet,
    latest: pd.DataFrame,
    fmt: dict[str, xlsxwriter.format.Format],
) -> None:
    sheet.set_row(0, 34)
    sheet.set_row(1, 18)
    sheet.merge_range("A1:S1", "US Bank Duration-Risk Screener", fmt["title"])
    sheet.merge_range(
        "A2:S2",
        "Values in $mm unless stated; raw source values at right remain in reported $000.",
        fmt["subtitle"],
    )
    headers = [
        "Review rank", "Score", "Score status", "Tier", "Coverage", "FDIC cert", "Bank", "State",
        "Assets ($mm)", "TCE ($mm)", "HTM loss ($mm)", "AFS loss ($mm)", "Combined loss / TCE",
        "Adjusted TCE ($mm)", "Uninsured concentration", "Deposit cost", "Deposit beta (4Q)",
        "Asset peer", "Absolute warning flags", "Bank equity ($000)", "Preferred ($000)",
        "Intangibles ($000)", "HTM amortized cost ($000)", "HTM fair value ($000)",
        "AFS amortized cost ($000)", "AFS fair value ($000)", "Uninsured deposits ($000)",
        "Domestic deposits ($000)",
    ]
    extra_columns = [
        ("cash_and_balances", "Cash ($000)", "number"),
        ("other_borrowings", "Other borrowings ($000)", "number"),
        ("avg_effr", "Average EFFR", "percent"),
        ("deposit_spread_compression_4q", "Funding spread compression (4Q)", "percent"),
        ("rate_regime", "Rate regime", "body"),
        ("deposit_growth_qoq", "Deposit growth QoQ", "percent"),
        ("cash_uninsured_coverage", "Cash / uninsured", "percent"),
        ("net_interest_margin", "Quarterly NIM", "percent"),
        ("borrowings_assets", "Borrowings / assets", "percent"),
        ("capital_alert", "Capital review priority", "body"),
        ("stress_funding_gap", "Scenario funding gap ($000)", "number"),
        ("stress_tce_after_sales", "Scenario TCE after sales ($000)", "number"),
        ("stress_replacement_funding_premium", "Scenario funding premium ($000)", "number"),
        ("combined_loss_tce_peer_count", "Loss peers", "number"),
        ("uninsured_concentration_peer_count", "Uninsured peers", "number"),
        ("deposit_spread_compression_4q_peer_count", "Funding peers", "number"),
        ("deposit_cost_status", "Deposit-cost availability", "body"),
        ("beta_status", "Beta interpretation", "body"),
        ("funding_percentile", "Funding-pressure percentile", "score"),
        ("loss_funding_interaction", "Loss / funding interaction", "percent"),
    ]
    headers += [label for _, label, _ in extra_columns]
    header_row = 5
    for col, header in enumerate(headers):
        sheet.write(header_row, col, header, fmt["raw_header"] if col >= 19 else fmt["header"])
    sheet.set_row(header_row, 42)
    widths = [9, 9, 12, 9, 10, 10, 34, 7, 14, 14, 14, 14, 17, 17, 17, 13, 17, 12, 36] + [18] * (9 + len(extra_columns))
    for col, width in enumerate(widths):
        sheet.set_column(col, col, width)
    for idx, row in latest.iterrows():
        excel_row = header_row + 2 + idx
        zero_row = header_row + 1 + idx
        values = [
            row["risk_rank"], row["display_score"], row["score_status"], row["risk_tier"],
            row["score_coverage"], row["cert"], row["name"], row.get("state"),
            row["total_assets"] / 1000 if pd.notna(row["total_assets"]) else None,
        ]
        formats = [fmt["number"], fmt["score"], fmt["body"], fmt["body"], fmt["percent"], fmt["number"], fmt["body"], fmt["body"], fmt["money1"]]
        for col, (value, cell_fmt) in enumerate(zip(values, formats, strict=True)):
            _write_value(sheet, zero_row, col, value, cell_fmt)
        # Auditable Excel formulas with cached Python-calculated values.
        tce_mm = row["tce"] / 1000 if pd.notna(row["tce"]) else None
        htm_mm = row["htm_loss"] / 1000 if pd.notna(row["htm_loss"]) else None
        afs_mm = row["afs_loss"] / 1000 if pd.notna(row["afs_loss"]) else None
        adjusted_mm = row["adjusted_tce"] / 1000 if pd.notna(row["adjusted_tce"]) else None
        r = excel_row
        sheet.write_formula(zero_row, 9, f'=IF(COUNT(T{r}:V{r})=3,(T{r}-U{r}-V{r})/1000,"N/A")', fmt["money1"], _excel_value(tce_mm))
        sheet.write_formula(zero_row, 10, f'=IF(COUNT(W{r}:X{r})=2,MAX((W{r}-X{r})/1000,0),"N/A")', fmt["money1"], _excel_value(htm_mm))
        sheet.write_formula(zero_row, 11, f'=IF(COUNT(Y{r}:Z{r})=2,MAX((Y{r}-Z{r})/1000,0),"N/A")', fmt["money1"], _excel_value(afs_mm))
        sheet.write_formula(zero_row, 12, f'=IF(AND(COUNT(J{r}:L{r})=3,J{r}>0),(K{r}+L{r})/J{r},"N/A")', fmt["percent"], _excel_value(row["combined_loss_tce"]))
        sheet.write_formula(zero_row, 13, f'=IF(COUNT(J{r}:K{r})=2,J{r}-K{r},"N/A")', fmt["money1"], _excel_value(adjusted_mm))
        sheet.write_formula(zero_row, 14, f'=IF(AND(COUNT(AA{r}:AB{r})=2,AB{r}>0,AA{r}>=0,AA{r}<=AB{r}),AA{r}/AB{r},"N/A")', fmt["percent"], _excel_value(row["uninsured_concentration"]))
        for col, key, cell_fmt in ((15, "deposit_cost", fmt["percent"]), (16, "deposit_beta_4q", fmt["percent"]), (17, "asset_peer", fmt["body"]), (18, "risk_flags", fmt["body"])):
            _write_value(sheet, zero_row, col, row.get(key), cell_fmt)
        raw_keys = ["bank_equity", "preferred_equity", "intangible_assets", "htm_amortized_cost", "htm_fair_value", "afs_amortized_cost", "afs_fair_value", "uninsured_deposits", "domestic_deposits"]
        for col, key in enumerate(raw_keys, start=19):
            _write_value(sheet, zero_row, col, row.get(key), fmt["number"])
        for col, (key, _, format_key) in enumerate(extra_columns, start=28):
            value = row.get(key)
            sheet.write(zero_row, col, _excel_value(value), fmt[format_key])
    if len(latest):
        sheet.add_table(header_row, 0, header_row + len(latest), len(headers) - 1, {"name": "BankScreener", "style": "Table Style Medium 2", "columns": [{"header": h, "header_format": fmt["raw_header"] if i >= 19 else fmt["header"]} for i, h in enumerate(headers)]})
        sheet.conditional_format(header_row + 1, 1, header_row + len(latest), 1, {"type": "3_color_scale", "min_color": "#E2F0D9", "mid_color": "#FFF2CC", "max_color": "#F4CCCC"})
        sheet.conditional_format(header_row + 1, 3, header_row + len(latest), 3, {"type": "text", "criteria": "containing", "value": "Red", "format": fmt["fail"]})
        sheet.conditional_format(header_row + 1, 3, header_row + len(latest), 3, {"type": "text", "criteria": "containing", "value": "Amber", "format": fmt["warn"]})
        sheet.conditional_format(header_row + 1, 13, header_row + len(latest), 13, {"type": "cell", "criteria": "<", "value": 0, "format": fmt["fail"]})
    sheet.freeze_panes(header_row + 1, 7)
    sheet.set_tab_color("#C00000")


def _history_columns() -> list[tuple[str, str]]:
    return [
        ("cert", "FDIC cert"), ("report_date", "Report date"), ("name", "Bank"), ("state", "State"),
        ("total_assets", "Assets ($000)"), ("domestic_deposits", "Domestic deposits ($000)"),
        ("bank_equity", "Bank equity ($000)"), ("preferred_equity", "Preferred ($000)"),
        ("intangible_assets", "Intangibles ($000)"), ("tce", "TCE ($000)"),
        ("htm_loss", "HTM loss ($000)"), ("afs_loss", "AFS loss ($000)"),
        ("combined_loss_tce", "Combined loss / TCE"), ("uninsured_concentration", "Uninsured concentration"),
        ("deposit_cost", "Deposit cost"), ("deposit_beta_4q", "Deposit beta (4Q)"),
        ("avg_effr", "Average EFFR"), ("partial_risk_score", "Display score"),
        ("risk_score", "Full score"), ("score_coverage", "Coverage"), ("risk_tier", "Tier"),
        ("risk_flags", "Flags"), ("source_url", "Source URL"),
        ("lookup_key", "Bank-quarter key"), ("htm_loss_tce", "HTM loss / TCE"),
        ("afs_loss_tce", "AFS loss / TCE"),
        ("deposit_spread_compression_4q", "Funding spread compression"),
        ("cash_uninsured_coverage", "Cash / uninsured"), ("net_interest_margin", "Quarterly NIM"),
    ]


def _write_history(sheet: xlsxwriter.worksheet.Worksheet, frame: pd.DataFrame, fmt: dict[str, xlsxwriter.format.Format]) -> None:
    frame = frame.copy()
    # Numeric key avoids text-lookup coercion differences across spreadsheet engines.
    frame["lookup_key"] = frame["cert"].astype("int64") * 100_000_000 + pd.to_datetime(frame["report_date"]).dt.strftime("%Y%m%d").astype("int64")
    columns = _history_columns()
    sheet.write_row(0, 0, [label for _, label in columns], fmt["header"])
    for row_idx, (_, row) in enumerate(frame.sort_values(["cert", "report_date"]).iterrows(), start=1):
        for col_idx, (key, _) in enumerate(columns):
            value = row.get(key)
            if key == "report_date" and value is not None:
                sheet.write_datetime(row_idx, col_idx, value, fmt["date"])
            elif _is_missing(value):
                sheet.write_string(row_idx, col_idx, "N/A", fmt["body"])
            elif isinstance(value, str):
                sheet.write_string(row_idx, col_idx, value, fmt["external"] if key == "source_url" else fmt["body"])
            else:
                cell_fmt = fmt["percent"] if key in {"combined_loss_tce", "uninsured_concentration", "deposit_cost", "deposit_beta_4q", "avg_effr", "score_coverage"} else fmt["number"]
                sheet.write_number(row_idx, col_idx, float(value), cell_fmt)
    if len(frame):
        sheet.add_table(0, 0, len(frame), len(columns) - 1, {"name": "BankHistory", "style": "Table Style Medium 2", "columns": [{"header": label, "header_format": fmt["header"]} for _, label in columns]})
    sheet.set_row(0, 34)
    sheet.set_column("A:A", 11)
    sheet.set_column("B:B", 12)
    sheet.set_column("C:C", 34)
    sheet.set_column("D:D", 7)
    sheet.set_column("E:V", 18)
    sheet.set_column("W:W", 48)
    sheet.set_column("X:AC", 22)
    sheet.freeze_panes(1, 4)
    sheet.set_tab_color("#70AD47")


def _write_detail(
    workbook: xlsxwriter.Workbook,
    sheet: xlsxwriter.worksheet.Worksheet,
    frame: pd.DataFrame,
    latest: pd.DataFrame,
    default_cert: int,
    fmt: dict[str, xlsxwriter.format.Format],
) -> None:
    sheet.set_column("A:A", 16)
    sheet.set_column("B:B", 18)
    sheet.set_column("C:J", 17)
    sheet.set_row(0, 34)
    sheet.merge_range("A1:J1", "Bank Detail — Eight-Quarter Trend", fmt["title"])
    sheet.write("A4", "FDIC certificate", fmt["label"])
    sheet.write_number("B4", default_cert, fmt["input"])
    if not latest.empty:
        workbook.define_name("FDICCertList", f"=\'Screener\'!$F$7:$F${len(latest) + 6}")
        sheet.data_validation("B4", {"validate": "list", "source": "=FDICCertList"})
    default_latest = latest.loc[latest["cert"].eq(default_cert)].iloc[0] if default_cert and not latest.empty else None
    formulas = [
        ("A5", "Bank", "=IFERROR(INDEX('Screener'!$G$7:$G$10000,MATCH($B$4,'Screener'!$F$7:$F$10000,0)),\"Not found\")", None if default_latest is None else default_latest["name"]),
        ("A6", "State", "=IFERROR(INDEX('Screener'!$H$7:$H$10000,MATCH($B$4,'Screener'!$F$7:$F$10000,0)),\"\")", None if default_latest is None else default_latest.get("state")),
        ("A7", "Risk tier", "=IFERROR(INDEX('Screener'!$D$7:$D$10000,MATCH($B$4,'Screener'!$F$7:$F$10000,0)),\"N/A\")", None if default_latest is None else default_latest["risk_tier"]),
        ("A8", "Risk score", "=IFERROR(INDEX('Screener'!$B$7:$B$10000,MATCH($B$4,'Screener'!$F$7:$F$10000,0)),NA())", None if default_latest is None else default_latest["display_score"]),
    ]
    for cell, label, formula, cached in formulas:
        row = int(cell[1:]) - 1
        sheet.write(row, 0, label, fmt["label"])
        sheet.write_formula(row, 1, formula, fmt["score"] if label == "Risk score" else fmt["formula"], _excel_value(cached))
    headers = ["Report date", "Assets ($mm)", "TCE ($mm)", "HTM loss/TCE", "AFS loss/TCE", "Combined loss/TCE", "Uninsured", "Deposit cost", "Deposit beta", "Risk score"]
    sheet.write_row(11, 0, headers, fmt["header"])
    default_history = frame.loc[frame["cert"].eq(default_cert)].set_index("report_date")
    periods = sorted(frame["report_date"].unique())[-8:]
    history_last = len(frame) + 1
    mappings = [("E", 1000, "total_assets"), ("J", 1000, "tce"),
        ("Y", 1, "htm_loss_tce"), ("Z", 1, "afs_loss_tce"),
        ("M", 1, "combined_loss_tce"), ("N", 1, "uninsured_concentration"),
        ("O", 1, "deposit_cost"), ("P", 1, "deposit_beta_4q"), ("R", 1, "partial_risk_score")]
    for i, period in enumerate(periods):
        row_idx = 12 + i
        sheet.write_datetime(row_idx, 0, period, fmt["date"])
        cached_row = default_history.loc[period] if period in default_history.index else None
        for col_idx, (history_col, divisor, key) in enumerate(mappings, start=1):
            excel_row = row_idx + 1
            lookup = f'INDEX(History!${history_col}$2:${history_col}${history_last},MATCH($B$4*100000000+YEAR($A{excel_row})*10000+MONTH($A{excel_row})*100+DAY($A{excel_row}),History!$X$2:$X${history_last},0))'
            formula = f'=IFERROR(IF(ISNUMBER({lookup}),{lookup}/{divisor},"N/A"),"N/A")'
            cached = None if cached_row is None else cached_row.get(key)
            cell_fmt = fmt["money1"] if col_idx in {1, 2} else fmt["score"] if col_idx == 9 else fmt["percent"]
            sheet.write_formula(row_idx, col_idx, formula, cell_fmt, _excel_value(cached / divisor if cached is not None else cached))
    sheet.merge_range("D4:J5", "Bank selector also drives Stress Test. Raw beta is descriptive; the score now uses deposit-cost change minus EFFR change. Missing values remain N/A.", fmt["note"])
    chart1 = workbook.add_chart({"type": "line"})
    chart1.add_series({"name": "Combined loss / TCE", "categories": "='Bank Detail'!$A$13:$A$20", "values": "='Bank Detail'!$F$13:$F$20", "line": {"color": "#C00000", "width": 2.25}})
    chart1.add_series({"name": "Uninsured concentration", "categories": "='Bank Detail'!$A$13:$A$20", "values": "='Bank Detail'!$G$13:$G$20", "line": {"color": "#1F4E78", "width": 2.25}})
    chart1.set_title({"name": "Loss absorption and uninsured funding"})
    chart1.set_x_axis({"date_axis": True, "num_format": "yyyy-mm-dd"})
    chart1.set_y_axis({"num_format": "0%", "major_gridlines": {"visible": True, "line": {"color": "#E7E6E6"}}})
    chart1.set_legend({"position": "bottom"})
    chart1.set_style(10)
    sheet.insert_chart("A23", chart1, {"x_scale": 1.2, "y_scale": 1.15})
    chart2 = workbook.add_chart({"type": "line"})
    chart2.add_series({"name": "Deposit cost", "categories": "='Bank Detail'!$A$13:$A$20", "values": "='Bank Detail'!$H$13:$H$20", "line": {"color": "#70AD47", "width": 2.25}})
    chart2.set_title({"name": "Annualized quarterly deposit cost"})
    chart2.set_x_axis({"date_axis": True, "num_format": "yyyy-mm-dd"})
    chart2.set_y_axis({"num_format": "0.0%", "major_gridlines": {"visible": True, "line": {"color": "#E7E6E6"}}})
    chart2.set_legend({"none": True})
    chart2.set_style(10)
    sheet.insert_chart("F23", chart2, {"x_scale": 1.2, "y_scale": 1.15})
    sheet.freeze_panes(12, 1)
    sheet.set_tab_color("#5B9BD5")


def _write_methodology(sheet: xlsxwriter.worksheet.Worksheet, config: RiskModelConfig, fmt: dict[str, xlsxwriter.format.Format]) -> None:
    sheet.set_column("A:A", 28)
    sheet.set_column("B:B", 72)
    sheet.set_column("C:C", 22)
    sheet.set_row(0, 34)
    sheet.merge_range("A1:C1", "Methodology and Field Map", fmt["title"])
    rows = [
        ("Tangible common equity", "Bank equity − perpetual preferred equity − intangible assets", "RC 3210, 3838, 2143"),
        ("HTM unrealized loss", "MAX(HTM amortized cost − HTM fair value, 0)", "RC-B 1754, 1771"),
        ("AFS unrealized loss", "MAX(AFS amortized cost − AFS fair value, 0)", "RC-B 1772, 1773"),
        ("Adjusted TCE", "TCE − HTM unrealized loss; AFS is not deducted again", "Derived"),
        ("Uninsured concentration", "Estimated uninsured deposits ÷ domestic deposits", "RC-O 5597; RC 2200"),
        ("Deposit cost", "4 × quarterly domestic deposit interest expense ÷ average domestic deposits", "RI 4508, 0093, HK03, HK04"),
        ("Four-quarter deposit beta", "Δ deposit cost over four quarters ÷ Δ quarterly-average EFFR; N/A if |ΔEFFR| < 25 bps", "FRED DFF"),
        ("Risk score", "Peer percentiles: loss/TCE + uninsured + funding spread compression (weights below)", "Analytical default"),
    ]
    sheet.write_row("A3", ["Metric", "Definition", "Primary source fields"], fmt["header"])
    for row_idx, row in enumerate(rows, start=3):
        sheet.write_row(row_idx, 0, row, fmt["note"])
        sheet.set_row(row_idx, 32)
    sheet.merge_range("A14:C14", "Score interpretation", fmt["section"])
    notes = [
        "Scores are relative within asset peers (<$1B, $1–10B, $10–100B, ≥$100B), not probability-of-default estimates.",
        "Full scores require all three components. Partial scores reweight only available components and disclose coverage; missing uninsured deposits are never set to zero.",
        "Absolute warning thresholds are configurable and are not supervisory classifications or regulatory limits.",
        "Call Reports describe bank legal entities, not consolidated bank holding companies. Results are quarterly and backward-looking.",
    ]
    for idx, note in enumerate(notes, start=15):
        sheet.merge_range(idx, 0, idx, 2, note, fmt["note"])
        sheet.set_row(idx, 30)
    sheet.merge_range("A21:C21", "Configurable defaults", fmt["section"])
    config_rows = [
        ("Combined loss/TCE weight", config.weights["combined_loss_tce"]),
        ("Uninsured concentration weight", config.weights["uninsured_concentration"]),
        ("Funding compression weight", config.weights["deposit_spread_compression_4q"]),
        ("Minimum EFFR change (bps)", config.beta_min_rate_change_bps),
    ]
    for idx, (label, value) in enumerate(config_rows, start=22):
        sheet.write(idx, 0, label, fmt["label"])
        sheet.write_number(idx, 1, value, fmt["number"] if "bps" in label else fmt["percent"])
    notes = [
        "Funding compression = four-quarter change in deposit cost minus change in EFFR. Higher is worse in rising or falling rates; this is not an observed asset spread or NIM.",
        "Beta is descriptive, including in mixed-rate windows. N/A below the configured EFFR-change cutoff. Comparisons require six consecutive quarter-end filings, with no flagged balance discontinuity.",
        "Quarterly NIM (FDIC NIMYQ / 100), cash/uninsured coverage (CHBAL), borrowing share (OTHBOR / ASSET), deposit outflow and joint loss/funding flags are diagnostics, not extra score weights.",
        f"Balance changes above {config.balance_jump_threshold:.0%} suppress comparable deposit costs; this flags possible mergers or flows, without inferring their cause. Nonpositive capital takes review priority.",
        "Stress Test uses cash, assumed borrowing, AFS sales, then permitted HTM sales. Remaining cash gaps are not filled automatically. Equity changes are pre-tax proxies, excluding hedges, credit allowances and taxes.",
        "Weights, tiers, usable shares and runoff assumptions are analytical choices—not calibrated probabilities, regulatory thresholds, LCR or full economic-value-of-equity measures. Read docs/methodology.md.",
    ]
    for note_row, text in enumerate(notes, start=28):
        sheet.merge_range(note_row, 0, note_row, 2, text, fmt["note"])
        sheet.set_row(note_row, 36)
    sheet.set_tab_color("#A5A5A5")


def _write_checks(sheet: xlsxwriter.worksheet.Worksheet, latest: pd.DataFrame, frame: pd.DataFrame, fmt: dict[str, xlsxwriter.format.Format]) -> None:
    sheet.set_column("A:A", 34)
    sheet.set_column("B:E", 16)
    sheet.set_column("F:F", 48)
    sheet.set_row(0, 34)
    sheet.merge_range("A1:F1", "Model Checks", fmt["title"])
    sheet.write_row("A3", ["Assertion", "Actual", "Expected", "Difference", "Status", "Notes"], fmt["header"])
    duplicate_count = int(frame.duplicated(["cert", "report_date", "source"]).sum())
    checks = [
        ("No duplicate bank-quarter-source rows", duplicate_count, 0, "Primary-key integrity"),
        ("Latest-quarter population is nonzero", len(latest), "> 0", "Universe loaded"),
        ("Negative total assets", int((latest["total_assets"] < 0).sum()), 0, "Units/sign sanity"),
        ("Losses below zero", int(((latest["htm_loss"] < 0) | (latest["afs_loss"] < 0)).sum()), 0, "MAX(loss, 0) rule"),
        ("Full score coverage does not exceed 100%", int((latest["score_coverage"] > 1.000001).sum()), 0, "Weight completeness"),
        ("Source URLs populated", int(latest["source_url"].isna().sum()), 0, "Data lineage"),
        ("Waterfall residuals above $0.01", int(latest["stress_waterfall_residual"].abs().gt(0.00001).sum()), 0, "Available scenarios reconcile; $000 internal units"),
    ]
    for idx, (assertion, actual, expected, note) in enumerate(checks, start=3):
        sheet.write(idx, 0, assertion, fmt["note"])
        sheet.set_row(idx, 30)
        sheet.write(idx, 1, actual, fmt["number"])
        sheet.write(idx, 2, expected, fmt["body"])
        difference = actual if expected == 0 else int(actual <= 0)
        sheet.write_number(idx, 3, difference, fmt["number"])
        ok = (actual == 0) if expected == 0 else (actual > 0)
        sheet.write_formula(idx, 4, f'=IF(D{idx + 1}=0,"OK","REVIEW")', fmt["ok"] if ok else fmt["warn"], "OK" if ok else "REVIEW")
        sheet.write(idx, 5, note, fmt["note"])
    overall_ok = all(((actual == 0) if expected == 0 else (actual > 0)) for _, actual, expected, _ in checks)
    sheet.write("A12", "Overall model status", fmt["label"])
    sheet.write_formula("B12", f'=IF(COUNTIF(E4:E{3 + len(checks)},"REVIEW")=0,"OK","REVIEW")', fmt["ok"] if overall_ok else fmt["warn"], "OK" if overall_ok else "REVIEW")
    additional = [
        ("Missing uninsured observations (allowed)", int(latest["uninsured_concentration"].isna().sum())),
        ("Unavailable stress runs (allowed)", int(latest["stress_status"].eq("Unavailable").sum())),
        ("Waterfall residuals above $0.01 ($000)", int(latest["stress_waterfall_residual"].abs().gt(0.00001).sum())),
        ("Capital alerts requiring review", int(latest["review_priority"].sum())),
    ]
    for row, (label, value) in enumerate(additional, start=14):
        sheet.merge_range(row, 0, row, 2, label, fmt["note"])
        sheet.write_number(row, 3, value, fmt["number"])
    sheet.merge_range("A20:F21", "OK means these mechanical checks passed, not that banks or the methodology are safe. Review missing-data coverage and capital alerts separately.", fmt["note"])
    sheet.set_tab_color("#70AD47" if overall_ok else "#FFC000")


def _write_sources(sheet: xlsxwriter.worksheet.Worksheet, sources: pd.DataFrame | None, fmt: dict[str, xlsxwriter.format.Format]) -> None:
    sheet.set_column("A:A", 24)
    sheet.set_column("B:C", 14)
    sheet.set_column("D:D", 12)
    sheet.set_column("E:E", 32)
    sheet.set_column("F:F", 72)
    sheet.set_row(0, 34)
    sheet.merge_range("A1:F1", "Sources and Data Lineage", fmt["title"])
    sheet.write_row("A3", ["Source", "First period", "Last period", "Rows", "Retrieved at", "URL"], fmt["header"])
    if sources is not None and not sources.empty:
        for idx, row in sources.reset_index(drop=True).iterrows():
            out = idx + 3
            values = [row.get("source"), row.get("first_period"), row.get("last_period"), row.get("rows"), row.get("retrieved_at"), row.get("source_url")]
            for col, value in enumerate(values):
                sheet.write(out, col, _excel_value(value), fmt["external"] if col == 5 else fmt["body"])
    static = [
        ("FDIC BankFind API documentation", FDIC_URL),
        ("FFIEC CDR Public Web Service", FFIEC_URL),
        ("Current FFIEC 031 reporting form", FFIEC_FORM_URL),
        ("FRED Effective Federal Funds Rate", FRED_URL),
        ("FDIC financial field definitions", "https://api.fdic.gov/banks/docs/risview_properties.yaml"),
        ("Fed: funding-cost pass-through", "https://www.federalreserve.gov/econres/feds/monetary-policy-and-bank-funding-costs.htm"),
        ("NBER: losses and uninsured runs", "https://www.nber.org/papers/w31048"),
    ]
    start = 6 + (0 if sources is None else len(sources))
    sheet.merge_range(start, 0, start, 5, "Primary documentation", fmt["section"])
    for offset, (name, url) in enumerate(static, start=1):
        sheet.write(start + offset, 0, name, fmt["body"])
        sheet.merge_range(start + offset, 1, start + offset, 5, url, fmt["external"])
    sheet.set_tab_color("#ED7D31")
