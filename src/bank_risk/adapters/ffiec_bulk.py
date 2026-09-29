from __future__ import annotations

import hashlib
import re
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

from bank_risk.adapters.ffiec import parse_sdf_text, row_to_call_report
from bank_risk.dates import parse_date
from bank_risk.models import CallReport

FFIEC_BULK_URL = "https://cdr.ffiec.gov/public/PWS/DownloadBulkData.aspx"


def _date_from_name(name: str) -> date | None:
    match = re.search(r"(20\d{6})", name)
    return parse_date(match.group(1)) if match else None


def import_bulk_zip(path: Path, report_date: date | None = None) -> list[CallReport]:
    payload_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    retrieved_at = datetime.now(UTC)
    reports: list[CallReport] = []
    with zipfile.ZipFile(path) as archive:
        candidates = [
            name
            for name in archive.namelist()
            if name.lower().endswith((".txt", ".sdf", ".tsv", ".csv")) and not name.endswith("/")
        ]
        if not candidates:
            raise ValueError("FFIEC bulk ZIP contains no delimited text file")
        for name in candidates:
            content = archive.read(name)
            rows = parse_sdf_text(content.decode("utf-8-sig", errors="replace"))
            period = report_date or _date_from_name(name) or _date_from_name(path.name)
            if period is None:
                raise ValueError("Report date not found; pass --as-of YYYY-MM-DD")
            for row in rows:
                report = row_to_call_report(
                    row,
                    report_date=period,
                    source="ffiec-bulk",
                    source_url=FFIEC_BULK_URL,
                    payload_hash=payload_hash,
                    retrieved_at=retrieved_at,
                )
                if report is not None:
                    reports.append(report)
    if not reports:
        raise ValueError("No bank Call Report rows were parsed from the FFIEC bulk ZIP")
    return reports

