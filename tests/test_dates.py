from datetime import date

import pytest

from bank_risk.dates import parse_date, previous_quarter, quarter_sequence


def test_parse_supported_dates() -> None:
    expected = date(2022, 12, 31)
    assert parse_date("2022-12-31") == expected
    assert parse_date("20221231") == expected
    assert parse_date("12/31/2022") == expected


def test_quarter_sequence_crosses_year() -> None:
    periods = quarter_sequence(date(2023, 6, 30), 4)
    assert periods == [
        date(2022, 9, 30),
        date(2022, 12, 31),
        date(2023, 3, 31),
        date(2023, 6, 30),
    ]
    assert previous_quarter(date(2023, 3, 31)) == date(2022, 12, 31)


def test_quarter_count_must_be_positive() -> None:
    with pytest.raises(ValueError):
        quarter_sequence(date(2023, 6, 30), 0)

