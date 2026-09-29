"""Editable Excel replica of the Python funding waterfall (reported $000)."""
from __future__ import annotations

from typing import Any

import pandas as pd
import xlsxwriter

from bank_risk.stress import StressScenario, simulate_bank


def write_stress(
    workbook: xlsxwriter.Workbook, sheet: Any, bank: dict[str, Any],
    bank_count: int, scenario: StressScenario, fmt: dict[str, Any],
) -> None:
    sheet.hide_gridlines(2)
    sheet.set_column("A:C", 12)
    sheet.set_column("D:E", 16)
    sheet.set_column("F:F", 3)
    sheet.set_column("G:I", 13)
    sheet.set_column("J:K", 18)
    sheet.set_row(0, 36)
    sheet.merge_range("A1:K1", "Funding Stress — Deposit-Run Waterfall", fmt["title"])
    sheet.merge_range("A2:K2", "Editable assumptions in blue. Balances in $000. Change the bank on Bank Detail; scenarios are not failure probabilities.", fmt["note"])
    sheet.merge_range("A3:C3", "Selected certificate", fmt["label"])
    sheet.write_formula("D3", "=SelectedFDICCert", fmt["number"], bank["cert"])
    sheet.merge_range("G3:K3", str(bank["name"]), fmt["note"])
    sheet.write_formula("G3", "='Bank Detail'!B5", fmt["note"], bank["name"])
    sheet.merge_range("A4:E4", "Scenario assumptions", fmt["section"])
    percent_input = workbook.add_format({"font_color": "#0000FF", "bg_color": "#FFF2CC", "border": 1, "num_format": "0.0%"})
    inputs = [
        ("Uninsured runoff", "uninsured_runoff"), ("Insured runoff", "insured_runoff"),
        ("Usable cash share", "cash_usable_fraction"), ("Usable AFS share", "afs_usable_fraction"),
        ("Permitted HTM share", "htm_usable_fraction"), ("AFS extra FV discount", "afs_sale_discount"),
        ("HTM extra FV discount", "htm_sale_discount"), ("New borrowing / assets", "replacement_capacity_pct_assets"),
        ("Borrowing spread (bps)", "replacement_spread_bps"), ("Premium horizon (months)", "horizon_months"),
    ]
    for row, (label, key) in enumerate(inputs, start=5):
        sheet.merge_range(row - 1, 0, row - 1, 2, label, fmt["body"])
        sheet.write_number(row - 1, 3, getattr(scenario, key), percent_input if row <= 12 else fmt["input"])
        maximum = 0.999999 if row in (10, 11) else 5000 if row == 13 else 60 if row == 14 else 1
        minimum = 0.000001 if row == 14 else 0
        sheet.data_validation(f"D{row}", {"validate": "decimal", "criteria": "between", "minimum": minimum, "maximum": maximum, "error_type": "stop", "error_message": "Enter a valid scenario assumption within the stated bounds."})
        workbook.define_name("Stress_" + key, f"='Stress Test'!$D${row}")
    sheet.merge_range("A16:E17", "Shares proxy for encumbrance and operational access. Borrowing capacity is incremental and assumed; default 0. No automatic central-bank support.", fmt["note"])
    sheet.merge_range("A18:E18", "Reported bank inputs ($000, except rates)", fmt["section"])
    bases = [
        ("Total assets", "total_assets", "I", 1000), ("Domestic deposits", "domestic_deposits", "AB", 1),
        ("Uninsured deposits", "uninsured_deposits", "AA", 1), ("Cash and balances", "cash_and_balances", "AC", 1),
        ("AFS fair value", "afs_fair_value", "Z", 1), ("HTM fair value", "htm_fair_value", "X", 1),
        ("HTM amortized cost", "htm_amortized_cost", "W", 1), ("Tangible common equity", "tce", "J", 1000),
        ("Quarter-average EFFR", "avg_effr", "AE", 1), ("Annualized deposit cost", "deposit_cost", "P", 1),
    ]
    last = bank_count + 6
    for row, (label, key, col, multiplier) in enumerate(bases, start=19):
        sheet.merge_range(row - 1, 0, row - 1, 2, label, fmt["body"])
        lookup = f"INDEX('Screener'!${col}$7:${col}${last},MATCH(SelectedFDICCert,'Screener'!$F$7:$F${last},0))"
        formula = f'=IFERROR(IF(ISNUMBER({lookup}),{lookup}*{multiplier},"N/A"),"N/A")'
        cached = bank.get(key)
        sheet.write_formula(f"D{row}", formula, fmt["percent"] if row >= 27 else fmt["number"], "N/A" if cached is None or pd.isna(cached) else cached)

    # Explicit guards also catch pasted invalid inputs, which can bypass Excel validation.
    guards = ["COUNT(D5:D14)=10", "MIN(D5:D13)>=0", "MAX(D5:D12)<=1", "D10<1", "D11<1", "D13<=5000", "D14>0", "D14<=60", "COUNT(D20:D21)=2", "D20>0", "D21>=0", "D21<=D20"]
    for fraction, cells in ((7, [22]), (8, [23]), (9, [24, 25]), (12, [19])):
        checks = ",".join(f"ISNUMBER(D{r}),D{r}>=0" for r in cells)
        guards.append(f"OR(D{fraction}=0,AND({checks}))")
    values = simulate_bank(bank, scenario)
    sheet.merge_range("G4:I4", "Input readiness", fmt["section"])
    sheet.merge_range("J4:K4", "", fmt["body"])
    sheet.write_formula("J4", '=IF(AND(' + ",".join(guards) + '),"Ready","Unavailable")', fmt["body"], "Unavailable" if values["stress_status"] == "Unavailable" else "Ready")
    formulas = [
        ("Deposit runoff", "deposit_runoff", "D21*D5+(D20-D21)*D6"),
        ("Usable cash", "cash_available", "IF(D7=0,0,D22*D7)"),
        ("Cash used", "cash_used", "MIN(J5,J6)"),
        ("Assumed new borrowing capacity", "borrowing_capacity", "IF(D12=0,0,D19*D12)"),
        ("Replacement borrowing", "replacement_borrowing", "MIN(MAX(J5-J7,0),J8)"),
        ("AFS sale capacity", "afs_capacity", "IF(D8=0,0,D23*D8*(1-D10))"),
        ("AFS cash proceeds", "afs_proceeds", "MIN(MAX(J5-J7-J9,0),J10)"),
        ("HTM sale capacity", "htm_capacity", "IF(D9=0,0,D24*D9*(1-D11))"),
        ("HTM cash proceeds", "htm_proceeds", "MIN(MAX(J5-J7-J9-J11,0),J12)"),
        ("Uncovered funding gap", "funding_gap", "MAX(J5-J7-J9-J11-J13,0)"),
        ("New AFS equity loss", "afs_incremental_loss", "J11/(1-D10)-J11"),
        ("Realized HTM loss proxy", "htm_realized_loss", "IF(J13=0,0,MAX(D25*(J13/(1-D11))/D24-J13,0))"),
        ("Incremental sale losses", "incremental_sale_loss", "J15+J16"),
        ("TCE after sales (pre-tax proxy)", "tce_after_sales", 'IF(ISNUMBER(D26),D26-J17,"N/A")'),
        ("Replacement funding rate", "replacement_rate", 'IF(ISNUMBER(D27),MAX(D27+D13/10000,0),"N/A")'),
        ("Incremental funding premium", "replacement_funding_premium", 'IF(J9=0,0,IF(AND(ISNUMBER(J19),ISNUMBER(D28)),J9*(J19-D28)*D14/12,"N/A"))'),
        ("TCE less funding premium", "tce_after_funding_premium", 'IF(COUNT(J18,J20)=2,J18-J20,"N/A")'),
        ("Waterfall reconciliation", "waterfall_residual", "J5-J7-J9-J11-J13-J14"),
    ]
    for row, (label, key, expression) in enumerate(formulas, start=5):
        sheet.merge_range(row - 1, 6, row - 1, 8, label, fmt["body"])
        cell_fmt = fmt["percent"] if row == 19 else fmt["number"]
        sheet.merge_range(row - 1, 9, row - 1, 10, "", cell_fmt)
        cached = values[f"stress_{key}"]
        sheet.write_formula(f"J{row}", f'=IF($J$4="Ready",{expression},"N/A")', cell_fmt, "N/A" if cached is None else cached)
        sheet.set_row(row - 1, 22)
    statuses = [
        (24, "Liquidity outcome", "status", '=IF(J4<>"Ready","Unavailable",IF(J14>0.00000001,"Funding gap","Covered under assumptions"))'),
        (25, "Equity outcome", "equity_status", '=IF(J4<>"Ready","Unavailable",IF(ISNUMBER(J18),IF(J18<=0,"Nonpositive","Positive"),"Missing TCE"))'),
        (26, "Funding-premium availability", "funding_premium_status", '=IF(J4<>"Ready","Unavailable",IF(ISNUMBER(J20),"Available","Missing EFFR or deposit cost"))'),
    ]
    for row, label, key, formula in statuses:
        sheet.merge_range(row - 1, 6, row - 1, 8, label, fmt["body"])
        sheet.merge_range(row - 1, 9, row - 1, 10, "", fmt["note"])
        sheet.write_formula(f"J{row}", formula, fmt["note"], values[f"stress_{key}"])
        sheet.set_row(row - 1, 30)
    sheet.merge_range("A30:K32", "Interpretation: a covered runoff is not proof of solvency. Existing AFS marks are already in TCE, so only the extra sale discount reduces equity here. HTM losses are charged only on the sold share. Residual funding gaps are left visible; asset sales never exceed assumed available balances.", fmt["note"])
    sheet.merge_range("A34:K36", "Limits: pre-tax, static balance sheet; no derivatives, loan marks, credit allowances, HTM reclassification effects, asset-yield loss, operational timing or ongoing earnings. The funding premium is only replacement borrowing cost less avoided deposit cost—not a forecast of total profit. Workbook edits do not update exported CSVs: rerun simulate with a scenario file.", fmt["note"])
    sheet.conditional_format("J14", {"type": "cell", "criteria": ">", "value": 0, "format": fmt["fail"]})
    sheet.conditional_format("J18", {"type": "cell", "criteria": "<=", "value": 0, "format": fmt["fail"]})
    sheet.freeze_panes(4, 0)
    sheet.set_tab_color("#7030A0")
