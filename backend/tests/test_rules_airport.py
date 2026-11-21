from datetime import date

import pytest

from app.extraction.models import ExtractedDocument
from app.extraction.parser import parse_text
from app.rules.airport import MissingAlert, check_airport_transfers
from app.rules.documents import RuleDocument
from app.rules.seed import load_cmi_default
from app.rules.transactions import TransactionDraft

_START = date(2026, 10, 1)
_END = date(2026, 10, 5)


def _draft(day, *, direction=None, airport="LAX", category="TRANS"):
    return TransactionDraft(
        category,
        "in_scope",
        "美洲",
        day,
        "Taxi",
        "USD",
        10,
        0,
        1,
        10,
        direction,
        airport,
        "missing_invoice",
        (),
        None,
        "receipt",
    )


def _trip(start=_START, end=_END, airports=("LAX",), doc_id="trip"):
    extracted = ExtractedDocument(
        doc_role="trip_application",
        trip_start_date=start,
        trip_end_date=end,
        destination_airports=list(airports),
        confidence=1,
        source="client_ocr",
    )
    return RuleDocument(doc_id, extracted)


def _check(drafts, trips=(), **updates):
    rules = load_cmi_default().compliance_rules.model_copy(update=updates)
    return check_airport_transfers(drafts, trips, rules)


def test_round_trip_fills_directions_and_keeps_input_immutable():
    drafts = (_draft(_START), _draft(_END))
    result = _check(drafts, (_trip(),))
    assert [draft.airport_direction for draft in result.drafts] == ["From", "To"]
    assert result.alerts == ()
    assert drafts[0].airport_direction is None


@pytest.mark.parametrize(
    ("present", "missing"),
    [
        ((), ("From", "To")),
        (("From",), ("To",)),
        (("To",), ("From",)),
    ],
)
def test_missing_slots_have_json_alerts(present, missing):
    drafts = tuple(
        _draft(_START if direction == "From" else _END, direction=direction)
        for direction in present
    )
    alerts = _check(drafts, (_trip(),)).alerts
    assert alerts == tuple(
        MissingAlert(direction, "LAX", _START if direction == "From" else _END)
        for direction in missing
    )
    if alerts:
        assert alerts[0].as_json()["code"] == "MISSING_AIRPORT_TRANSFER"


@pytest.mark.parametrize(
    ("offset", "tolerance", "matched"),
    [(1, 1, True), (-1, 1, True), (2, 1, False), (-2, 1, False), (1, 0, False)],
)
def test_calendar_day_tolerance(offset, tolerance, matched):
    result = _check(
        (_draft(date.fromordinal(_START.toordinal() + offset)),),
        (_trip(),),
        timezone_tolerance_days=tolerance,
    )
    assert (result.drafts[0].airport_direction == "From") is matched


def test_airport_mismatch_non_trans_and_duplicate_trips_leave_slots():
    trips = (_trip(airports=("LAX", "SJC", "LAX")), _trip(doc_id="again"))
    drafts = (
        _draft(_START, airport="HKG"),
        _draft(_START, category="Others", airport="SJC"),
        _draft(_START, direction="To"),
        _draft(_START, airport="LAX"),
        _draft(_END, airport="LAX"),
        _draft(_START, airport=None),
    )
    result = _check(drafts, trips)
    assert (result.drafts[:2], result.drafts[-1]) == (drafts[:2], drafts[-1])
    assert tuple(d.airport_direction for d in result.drafts[2:5]) == (
        "To",
        "From",
        "To",
    )
    assert [(alert.direction, alert.iata) for alert in result.alerts] == [
        ("From", "SJC"),
        ("To", "SJC"),
    ]
    ordered = _check((), (_trip(airports=("SJC", "LAX")),)).alerts
    assert tuple((a.direction, a.iata) for a in ordered) == (
        ("From", "LAX"),
        ("From", "SJC"),
        ("To", "LAX"),
        ("To", "SJC"),
    )


def test_single_day_prefers_from_then_to():
    day = date(2026, 10, 3)
    trip = _trip(day, day)
    one = _check((_draft(day),), (trip,))
    assert one.drafts[0].airport_direction == "From"
    assert [(alert.direction, alert.expected_date) for alert in one.alerts] == [
        ("To", day)
    ]
    two = _check((_draft(day), _draft(day)), (trip,))
    assert [draft.airport_direction for draft in two.drafts] == ["From", "To"]
    assert two.alerts == ()


def test_disabled_or_invalid_trip_does_not_infer():
    draft = _draft(_START)
    disabled = _check((draft,), (_trip(),), check_missing_airport_transfers=False)
    assert (disabled.drafts, disabled.alerts) == ((draft,), ())
    invalid = (_trip(None, _END), _trip(_END, _START), _trip(airports=()))
    result = _check((draft,), invalid)
    assert (result.drafts, result.alerts) == ((draft,), ())


def test_parser_trip_application_to_missing_transfer_integration():
    parsed = parse_text(
        "Business Trip\nStart: Oct 1, 2026\nEnd: Oct 5, 2026\nDestination Airport: LAX",
        source="client_ocr",
    )
    trip = RuleDocument("parsed-trip", parsed)
    assert parsed.trip_start_date == _START
    assert parsed.trip_end_date == _END
    assert parsed.destination_airports == ["LAX"]
    result = _check((_draft(_END, direction="To"),), (trip,))
    assert result.drafts[0].airport_direction == "To"
    assert result.alerts == (MissingAlert("From", "LAX", _START),)
