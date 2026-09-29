from __future__ import annotations

from datetime import date, datetime

QUARTER_ENDS = ((3, 31), (6, 30), (9, 30), (12, 31))


def parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported date format: {value}")


def quarter_end(value: date) -> date:
    for month, day in QUARTER_ENDS:
        candidate = date(value.year, month, day)
        if value <= candidate:
            return candidate
    return date(value.year, 12, 31)


def quarter_start(value: date) -> date:
    return date(value.year, ((value.month - 1) // 3) * 3 + 1, 1)


def previous_quarter(value: date) -> date:
    if value.month == 3:
        return date(value.year - 1, 12, 31)
    if value.month == 6:
        return date(value.year, 3, 31)
    if value.month == 9:
        return date(value.year, 6, 30)
    if value.month == 12:
        return date(value.year, 9, 30)
    return previous_quarter(quarter_end(value))


def quarter_sequence(as_of: date, count: int) -> list[date]:
    if count < 1:
        raise ValueError("count must be positive")
    current = quarter_end(as_of)
    result: list[date] = []
    for _ in range(count):
        result.append(current)
        current = previous_quarter(current)
    return list(reversed(result))
