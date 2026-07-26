import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fieldkit.human_duration import human_duration


def test_zero_renders_as_zero_seconds():
    assert human_duration(0) == "0s"


def test_seconds_only():
    assert human_duration(45) == "45s"


def test_minutes_and_seconds():
    assert human_duration(125) == "2m 5s"


def test_one_hour_exactly():
    assert human_duration(3600) == "1h"


def test_hours_minutes_and_seconds():
    assert human_duration(3725) == "1h 2m 5s"


def test_exact_hours_omit_zero_minutes_and_seconds():
    # Edge case from the spec: an exact number of hours drops both lower units.
    assert human_duration(7200) == "2h"
    assert human_duration(36000) == "10h"


def test_zero_units_in_the_middle_are_omitted():
    assert human_duration(3605) == "1h 5s"
    assert human_duration(3660) == "1h 1m"


def test_minute_boundary():
    assert human_duration(60) == "1m"
