from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from bank_risk.models import CallReport, MarketRate

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS institutions (
  cert INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  state TEXT,
  city TEXT,
  form_type TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS call_reports (
  cert INTEGER NOT NULL,
  report_date TEXT NOT NULL,
  source TEXT NOT NULL,
  total_assets REAL,
  total_deposits REAL,
  domestic_deposits REAL,
  bank_equity REAL,
  preferred_equity REAL,
  intangible_assets REAL,
  htm_amortized_cost REAL,
  htm_fair_value REAL,
  afs_amortized_cost REAL,
  afs_fair_value REAL,
  uninsured_deposits REAL,
  deposit_interest_expense_ytd REAL,
  source_url TEXT NOT NULL,
  retrieved_at TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  raw_json TEXT NOT NULL,
  PRIMARY KEY (cert, report_date, source),
  FOREIGN KEY (cert) REFERENCES institutions(cert)
);
CREATE TABLE IF NOT EXISTS market_rates (
  report_date TEXT PRIMARY KEY,
  avg_effr REAL NOT NULL,
  source TEXT NOT NULL,
  source_url TEXT NOT NULL,
  retrieved_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS metrics (
  cert INTEGER NOT NULL,
  report_date TEXT NOT NULL,
  model_version TEXT NOT NULL,
  metric_json TEXT NOT NULL,
  calculated_at TEXT NOT NULL,
  PRIMARY KEY (cert, report_date, model_version)
);
CREATE TABLE IF NOT EXISTS runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  as_of TEXT NOT NULL,
  quarters INTEGER NOT NULL,
  source TEXT NOT NULL,
  status TEXT NOT NULL,
  row_count INTEGER DEFAULT 0,
  message TEXT
);
CREATE INDEX IF NOT EXISTS idx_call_reports_date ON call_reports(report_date);
CREATE INDEX IF NOT EXISTS idx_metrics_date ON metrics(report_date);
"""


# This fixed column list is the only place that defines the report-upsert fields.
# Identifiers are code constants; all source values use named SQL parameters.
REPORT_COLUMNS = (
    "cert",
    "report_date",
    "source",
    "total_assets",
    "total_deposits",
    "domestic_deposits",
    "bank_equity",
    "preferred_equity",
    "intangible_assets",
    "htm_amortized_cost",
    "htm_fair_value",
    "afs_amortized_cost",
    "afs_fair_value",
    "uninsured_deposits",
    "deposit_interest_expense_ytd",
    "source_url",
    "retrieved_at",
    "payload_hash",
    "raw_json",
    "cash_and_balances",
    "other_borrowings",
    "net_interest_margin",
)
REPORT_UPSERT = f"""
    INSERT INTO call_reports ({", ".join(REPORT_COLUMNS)})
    VALUES ({", ".join(":" + column for column in REPORT_COLUMNS)})
    ON CONFLICT(cert, report_date, source) DO UPDATE SET
    {", ".join(f"{column}=excluded.{column}" for column in REPORT_COLUMNS[3:])}
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        # Additive migration preserves existing downloads and historical metrics.
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(call_reports)")}
        for column in ("cash_and_balances", "other_borrowings", "net_interest_margin"):
            if column not in columns:
                self.connection.execute(f"ALTER TABLE call_reports ADD COLUMN {column} REAL")
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def upsert_call_reports(self, reports: Iterable[CallReport]) -> int:
        now = datetime.now(UTC).isoformat()
        count = 0
        with self.connection:
            for report in reports:
                values = report.model_dump(mode="json")
                values["updated_at"] = now
                values["raw_json"] = json.dumps(values.pop("raw"), separators=(",", ":"))
                self.connection.execute(
                    """
                    INSERT INTO institutions(cert, name, state, city, form_type, updated_at)
                    VALUES(:cert, :name, :state, :city, :form_type, :updated_at)
                    ON CONFLICT(cert) DO UPDATE SET
                      name=excluded.name, state=excluded.state, city=excluded.city,
                      form_type=excluded.form_type, updated_at=excluded.updated_at
                    """,
                    values,
                )
                self.connection.execute(REPORT_UPSERT, values)
                count += 1
        return count

    def upsert_market_rates(self, rates: Iterable[MarketRate]) -> int:
        rows = [rate.model_dump(mode="json") for rate in rates]
        with self.connection:
            self.connection.executemany(
                """
                INSERT INTO market_rates(report_date, avg_effr, source, source_url, retrieved_at)
                VALUES(:report_date, :avg_effr, :source, :source_url, :retrieved_at)
                ON CONFLICT(report_date) DO UPDATE SET
                  avg_effr=excluded.avg_effr, source=excluded.source,
                  source_url=excluded.source_url, retrieved_at=excluded.retrieved_at
                """,
                rows,
            )
        return len(rows)

    def report_frame(self, source: str = "fdic") -> pd.DataFrame:
        query = """
        SELECT r.*, i.name, i.state, i.city, i.form_type
        FROM call_reports r JOIN institutions i USING(cert)
        WHERE r.source = ? ORDER BY r.cert, r.report_date
        """
        return pd.read_sql_query(query, self.connection, params=(source,))

    def rate_frame(self) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT report_date, avg_effr, source AS rate_source, source_url AS rate_source_url "
            "FROM market_rates ORDER BY report_date",
            self.connection,
        )

    def replace_metrics(self, frame: pd.DataFrame, model_version: str) -> None:
        calculated_at = datetime.now(UTC).isoformat()
        with self.connection:
            for record in frame.to_dict(orient="records"):
                clean = {
                    key: (
                        None
                        if pd.isna(value)
                        else value.item()
                        if hasattr(value, "item")
                        else value
                    )
                    for key, value in record.items()
                }
                self.connection.execute(
                    """
                    INSERT INTO metrics(cert, report_date, model_version, metric_json, calculated_at)
                    VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(cert, report_date, model_version) DO UPDATE SET
                      metric_json=excluded.metric_json, calculated_at=excluded.calculated_at
                    """,
                    (
                        int(clean["cert"]),  # type: ignore[arg-type]
                        str(clean["report_date"]),
                        model_version,
                        json.dumps(clean, default=str, separators=(",", ":")),
                        calculated_at,
                    ),
                )

    def start_run(self, as_of: date, quarters: int, source: str) -> int:
        cursor = self.connection.execute(
            "INSERT INTO runs(started_at, as_of, quarters, source, status) VALUES(?, ?, ?, ?, ?)",
            (datetime.now(UTC).isoformat(), as_of.isoformat(), quarters, source, "running"),
        )
        self.connection.commit()
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return a run ID")
        return int(cursor.lastrowid)

    def finish_run(self, run_id: int, status: str, row_count: int, message: str = "") -> None:
        self.connection.execute(
            "UPDATE runs SET completed_at=?, status=?, row_count=?, message=? WHERE run_id=?",
            (datetime.now(UTC).isoformat(), status, row_count, message, run_id),
        )
        self.connection.commit()

    def source_manifest(
        self, as_of: date | None = None, source: str = "fdic", first: date = date.min
    ) -> pd.DataFrame:
        return pd.read_sql_query(
            """
            SELECT source, MIN(report_date) AS first_period, MAX(report_date) AS last_period,
                   COUNT(*) AS rows, MAX(retrieved_at) AS retrieved_at,
                   MIN(source_url) AS source_url
            FROM call_reports WHERE source = ? AND report_date BETWEEN ? AND ? GROUP BY source
            UNION ALL
            SELECT source, MIN(report_date), MAX(report_date), COUNT(*), MAX(retrieved_at),
                   MIN(source_url) FROM market_rates WHERE report_date BETWEEN ? AND ? GROUP BY source
            """,
            self.connection,
            params=(source, str(first), str(as_of or date.max), str(first), str(as_of or date.max)),
        )

    def duplicate_count(self) -> int:
        row = self.connection.execute(
            """
            SELECT COUNT(*) FROM (
              SELECT cert, report_date, source, COUNT(*) AS n FROM call_reports
              GROUP BY cert, report_date, source HAVING n > 1
            )
            """
        ).fetchone()
        return int(row[0])
