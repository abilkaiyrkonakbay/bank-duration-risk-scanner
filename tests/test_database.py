from __future__ import annotations

from bank_risk.db import Database
from bank_risk.sample_data import demo_dataset


def test_call_report_upsert_is_idempotent(settings) -> None:  # type: ignore[no-untyped-def]
    reports, rates = demo_dataset()
    with Database(settings.db_path) as db:
        db.upsert_call_reports(reports)
        db.upsert_call_reports(reports)
        db.upsert_market_rates(rates)
        assert len(db.report_frame()) == len(reports)
        assert len(db.rate_frame()) == len(rates)
        assert db.duplicate_count() == 0
        actual = db.report_frame().iloc[0]
        expected = next(r for r in reports if r.cert == actual["cert"] and str(r.report_date) == actual["report_date"])
        assert actual["cash_and_balances"] == expected.cash_and_balances
        assert actual["other_borrowings"] == expected.other_borrowings
        assert actual["net_interest_margin"] == expected.net_interest_margin


def test_legacy_schema_migration_preserves_existing_rows(settings) -> None:  # type: ignore[no-untyped-def]
    reports, _ = demo_dataset()
    with Database(settings.db_path) as db:
        db.upsert_call_reports(reports)
        # Recreate the v0.1 schema in this disposable fixture database.
        for column in ("cash_and_balances", "other_borrowings", "net_interest_margin"):
            db.connection.execute(f"ALTER TABLE call_reports DROP COLUMN {column}")
        db.connection.commit()
    with Database(settings.db_path) as migrated:
        assert len(migrated.report_frame()) == len(reports)
        assert migrated.report_frame()["cash_and_balances"].isna().all()
        migrated.upsert_call_reports(reports)
        assert migrated.report_frame()["cash_and_balances"].notna().all()
