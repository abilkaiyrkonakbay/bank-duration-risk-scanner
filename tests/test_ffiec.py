from __future__ import annotations

import zipfile
from datetime import date
from pathlib import Path

from bank_risk.adapters.ffiec import parse_sdf_text, row_to_call_report
from bank_risk.adapters.ffiec_bulk import import_bulk_zip

WIDE = (
    "CERT;NAME;RCFD2170;RCON2200;RCON3210;RCON3838;RCFD2143;RCFD1754;RCFD1771;"
    "RCFD1772;RCFD1773;RCON5597;RIAD4508;RIAD0093;RIADHK03;RIADHK04\n"
    "24735;SILICON VALLEY BANK;209026000;161479000;15456000;0;285000;91327000;"
    "76168000;28502000;25970000;151592000;100000;200000;300000;194000\n"
)


def test_parse_wide_sdf_and_map_report() -> None:
    row = parse_sdf_text(WIDE)[0]
    report = row_to_call_report(
        row,
        report_date=date(2022, 12, 31),
        source="test",
        source_url="https://example.test",
        payload_hash="abc",
        retrieved_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
    )
    assert report is not None
    assert report.cert == 24735
    assert report.deposit_interest_expense_ytd == 794000
    assert report.afs_fair_value == 25_970_000


def test_import_bulk_zip_is_resumable_input(tmp_path: Path) -> None:
    path = tmp_path / "FFIEC_20221231.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("FFIEC Call Bulk 20221231.txt", WIDE)
    reports = import_bulk_zip(path)
    assert len(reports) == 1
    assert reports[0].report_date == date(2022, 12, 31)

