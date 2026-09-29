from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bank_risk.adapters.ffiec import FfiecClient
from bank_risk.adapters.ffiec_bulk import import_bulk_zip
from bank_risk.dates import parse_date
from bank_risk.db import Database
from bank_risk.models import AppSettings
from bank_risk.pipeline import calculate_and_export, load_demo, scan, simulate
from bank_risk.stress import load_scenario

app = typer.Typer(
    name="bank-risk",
    help="Screen US bank Call Reports for SVB-style duration and funding fragility.",
    no_args_is_help=True,
)


def _settings() -> AppSettings:
    return AppSettings.model_validate({})


@app.command("scan")
def scan_command(
    as_of: Annotated[str, typer.Option("--as-of", help="latest or YYYY-MM-DD")] = "latest",
    quarters: Annotated[int, typer.Option("--quarters", min=5, max=40)] = 8,
    source: Annotated[str, typer.Option("--source", help="All-bank source (fdic)")] = "fdic",
    refresh: Annotated[bool, typer.Option("--refresh", help="Ignore cached API payloads")] = False,
) -> None:
    """Download, calculate, persist, and export the all-bank screen."""
    settings = _settings()
    selected = None if as_of.lower() == "latest" else parse_date(as_of)
    result = scan(settings, selected, quarters, source, use_cache=not refresh)
    typer.echo(f"Screened {result.bank_count:,} banks as of {result.as_of}")
    typer.echo(f"SQLite: {result.database}")
    typer.echo(f"Workbook: {result.workbook}")
    typer.echo(f"Detailed CSV: {result.detailed_csv}")
    typer.echo(f"Top-risk CSV: {result.top_risk_csv}")
    typer.echo(f"Stress CSV: {result.stress_csv}")
    typer.echo(f"Run manifest: {result.manifest}")


@app.command("demo")
def demo_command() -> None:
    """Generate the complete output set from the deterministic SVB case-study fixture."""
    settings = _settings()
    settings = settings.model_copy(
        update={"db_path": settings.db_path.with_name("demo_bank_risk.sqlite")}
    )
    result = load_demo(settings)
    typer.echo(f"Demo complete: {result.workbook}")


@app.command("verify-ffiec")
def verify_ffiec(
    cert: Annotated[int, typer.Option("--cert", min=1)],
    as_of: Annotated[str, typer.Option("--as-of")],
) -> None:
    """Retrieve one authenticated FFIEC SDF facsimile and show mapped values."""
    settings = _settings()
    if not settings.ffiec_user_id or not settings.ffiec_token:
        raise typer.BadParameter("Set FFIEC_USER_ID and FFIEC_TOKEN in .env or the environment")
    report = FfiecClient(
        settings.ffiec_user_id,
        settings.ffiec_token,
        settings.cache_dir,
    ).verify(cert, parse_date(as_of))
    with Database(settings.db_path) as db:
        db.upsert_call_reports([report])
    typer.echo(f"Verified {report.name} ({report.cert}) for {report.report_date}")
    typer.echo(f"HTM AC/FV: {report.htm_amortized_cost} / {report.htm_fair_value} ($000)")
    typer.echo(f"AFS AC/FV: {report.afs_amortized_cost} / {report.afs_fair_value} ($000)")
    typer.echo(f"Uninsured deposits: {report.uninsured_deposits} ($000)")


@app.command("import-ffiec-bulk")
def import_bulk_command(
    zip_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    as_of: Annotated[str | None, typer.Option("--as-of")] = None,
) -> None:
    """Import an official FFIEC single-period bulk ZIP into SQLite."""
    reports = import_bulk_zip(zip_path, report_date=parse_date(as_of) if as_of else None)
    with Database(_settings().db_path) as db:
        count = db.upsert_call_reports(reports)
    typer.echo(f"Imported {count:,} FFIEC Call Report rows")


@app.command("export")
def export_command(
    as_of: Annotated[str, typer.Option("--as-of")],
    source: Annotated[str, typer.Option("--source")] = "fdic",
) -> None:
    """Rebuild CSV and Excel outputs from the local SQLite database."""
    settings = _settings()
    with Database(settings.db_path) as db:
        result = calculate_and_export(db, settings, parse_date(as_of), source=source)
    typer.echo(f"Exported {result.bank_count:,} banks to {result.workbook.parent}")


@app.command("simulate")
def simulate_command(
    as_of: Annotated[str, typer.Option("--as-of")],
    scenario: Annotated[
        Path | None, typer.Option("--scenario", exists=True, dir_okay=False)
    ] = None,
    cert: Annotated[int | None, typer.Option("--cert", min=1)] = None,
    source: Annotated[str, typer.Option("--source")] = "fdic",
) -> None:
    """Apply a configurable deposit-run scenario to locally stored bank filings."""
    settings = _settings()
    result = simulate(
        settings, parse_date(as_of), load_scenario(scenario or settings.scenario_path), cert, source
    )
    typer.echo(f"Scenario results: {result}")
    typer.echo(f"Assumptions and source hashes: {result.with_suffix('.json')}")


@app.command("version")
def version_command() -> None:
    from bank_risk import __version__

    typer.echo(__version__)


if __name__ == "__main__":
    app()
