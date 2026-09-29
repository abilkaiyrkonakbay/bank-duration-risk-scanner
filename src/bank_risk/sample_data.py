from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime

from bank_risk.dates import quarter_sequence
from bank_risk.models import CallReport, MarketRate


def demo_dataset() -> tuple[list[CallReport], list[MarketRate]]:
    """Small deterministic fixture centered on SVB's 2022 Q4 public Call Report values."""
    periods = quarter_sequence(date(2022, 12, 31), 8)
    effr = [0.0007, 0.0008, 0.0008, 0.0012, 0.0012, 0.0076, 0.0220, 0.0365]
    rates = [
        MarketRate(
            report_date=period,
            avg_effr=value,
            source_url="https://fred.stlouisfed.org/series/DFF",
            retrieved_at=datetime.now(UTC),
        )
        for period, value in zip(periods, effr, strict=True)
    ]
    banks = [
        (24735, "SILICON VALLEY BANK", "CA", 209_026_000, 161_479_000, 15_456_000),
        (3511, "JPMORGAN CHASE BANK, NATIONAL ASSOCIATION", "OH", 3_201_942_000, 2_340_179_000, 292_977_000),
        (628, "FIRST COMMUNITY BANK", "TX", 850_000, 710_000, 78_000),
        (99991, "DEMO REGIONAL BANK", "NY", 25_000_000, 20_000_000, 2_100_000),
    ]
    reports: list[CallReport] = []
    for bank_index, (cert, name, state, assets, deposits, equity) in enumerate(banks):
        for i, period in enumerate(periods):
            growth = 0.88 + i * (0.12 / 7)
            htm_ac: float
            htm_fv: float
            afs_ac: float
            afs_fv: float
            uninsured: float | None
            expense_ytd: float
            domestic: float
            if cert == 24735:
                htm_ac = [84_500_000, 90_200_000, 95_600_000, 98_201_000, 98_714_000, 95_820_000, 93_292_000, 91_327_000][i]
                htm_fv = [85_100_000, 90_900_000, 96_100_000, 97_226_000, 91_668_000, 84_581_000, 77_367_000, 76_168_000][i]
                afs_ac = [25_100_000, 25_900_000, 26_600_000, 27_240_000, 27_186_000, 28_041_000, 29_402_000, 28_502_000][i]
                afs_fv = [25_300_000, 26_100_000, 26_750_000, 27_087_000, 25_890_000, 26_124_000, 26_613_000, 25_970_000][i]
                uninsured = [130_000_000, 145_000_000, 157_000_000, 166_017_000, 172_918_000, 165_319_000, 155_263_000, 151_592_000][i]
                expense_ytd = [7_000, 16_000, 31_000, 59_000, 20_000, 89_000, 316_000, 794_000][i]
                domestic = [140_000_000, 155_000_000, 168_000_000, 175_378_000, 182_802_000, 174_957_000, 165_341_000, 161_479_000][i]
            else:
                duration_factor = 0.03 + bank_index * 0.015
                htm_ac = assets * growth * 0.12
                htm_fv = htm_ac * (1 - duration_factor * max(i - 3, 0) / 4)
                afs_ac = assets * growth * 0.08
                afs_fv = afs_ac * (1 - duration_factor * 0.45 * max(i - 3, 0) / 4)
                domestic = deposits * growth
                uninsured = None if assets < 1_000_000 else domestic * (0.22 + bank_index * 0.08)
                quarterly_cost = [0.0005, 0.0005, 0.0006, 0.0007, 0.0008, 0.0015, 0.0035, 0.006][i]
                q_expense = quarterly_cost * domestic / 4
                previous_ytd = reports[-1].deposit_interest_expense_ytd if i and reports else 0
                expense_ytd = q_expense if period.month == 3 else (previous_ytd or 0) + q_expense
            raw = f"{cert}-{period.isoformat()}".encode()
            reports.append(
                CallReport(
                    cert=cert,
                    report_date=period,
                    source="fdic",
                    name=name,
                    state=state,
                    form_type="041" if assets >= 5_000_000 else "051",
                    total_assets=assets * growth,
                    total_deposits=domestic,
                    domestic_deposits=domestic,
                    bank_equity=equity * growth,
                    preferred_equity=0,
                    intangible_assets=285_000 if cert == 24735 and i == 7 else equity * growth * 0.01,
                    htm_amortized_cost=htm_ac,
                    htm_fair_value=htm_fv,
                    afs_amortized_cost=afs_ac,
                    afs_fair_value=afs_fv,
                    uninsured_deposits=uninsured,
                    deposit_interest_expense_ytd=expense_ytd,
                    # Illustrative simulator inputs, not historical SVB observations.
                    cash_and_balances=assets * growth * 0.04,
                    other_borrowings=assets * growth * 0.06,
                    net_interest_margin=0.028 - max(i - 3, 0) * 0.001,
                    source_url="https://api.fdic.gov/banks/financials",
                    retrieved_at=datetime.now(UTC),
                    payload_hash=hashlib.sha256(raw).hexdigest(),
                    raw={"demo": True},
                )
            )
    return reports, rates
