from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from typing import Literal

from app.rules.documents import RuleDocument
from app.rules.dsl import ComplianceRules
from app.rules.transactions import TransactionDraft

_Slot = tuple[Literal["To", "From"], str, date]


@dataclass(frozen=True, slots=True)
class MissingAlert:
    direction: Literal["To", "From"]
    iata: str
    expected_date: date
    code: Literal["MISSING_AIRPORT_TRANSFER"] = "MISSING_AIRPORT_TRANSFER"

    def as_json(self) -> dict[str, str]:
        return asdict(self) | {"expected_date": self.expected_date.isoformat()}


@dataclass(frozen=True, slots=True)
class AirportCheckResult:
    drafts: tuple[TransactionDraft, ...]
    alerts: tuple[MissingAlert, ...]


def _expected_slots(trips: Sequence[RuleDocument]) -> list[_Slot]:
    slots: list[_Slot] = []
    for trip in trips:
        extracted = trip.extracted
        start, end = extracted.trip_start_date, extracted.trip_end_date
        if start is None or end is None or end < start:
            continue
        for iata in dict.fromkeys(extracted.destination_airports):
            slots.extend((("From", iata, start), ("To", iata, end)))
    return list(dict.fromkeys(slots))


def _matching_slot(
    draft: TransactionDraft, slots: list[_Slot], occupied: set[int], tolerance: int
) -> int | None:
    iata = (draft.airport_iata or "").strip().upper()
    matches = [
        (
            abs((draft.tx_date - slot[2]).days),
            0 if slot[0] == "From" else 1,
            index,
        )
        for index, slot in enumerate(slots)
        if iata
        and all(
            (
                index not in occupied,
                slot[1] == iata,
                abs((draft.tx_date - slot[2]).days) <= tolerance,
                draft.airport_direction is None or draft.airport_direction == slot[0],
            )
        )
    ]
    return min(matches)[2] if matches else None


def check_airport_transfers(
    drafts: Sequence[TransactionDraft],
    trip_applications: Sequence[RuleDocument],
    rules: ComplianceRules,
) -> AirportCheckResult:
    original = tuple(drafts)
    if not rules.check_missing_airport_transfers:
        return AirportCheckResult(original, ())
    slots = _expected_slots(trip_applications)
    if not slots:
        return AirportCheckResult(original, ())
    result = list(original)
    occupied: set[int] = set()
    candidates = sorted(
        (draft.tx_date, index)
        for index, draft in enumerate(original)
        if all((draft.category == "TRANS", bool((draft.airport_iata or "").strip())))
    )
    for _, index in candidates:
        slot_index = _matching_slot(
            original[index], slots, occupied, rules.timezone_tolerance_days
        )
        if slot_index is not None:
            occupied.add(slot_index)
            result[index] = replace(
                original[index], airport_direction=slots[slot_index][0]
            )

    alerts = [
        MissingAlert(direction, iata, expected_date)
        for index, (direction, iata, expected_date) in enumerate(slots)
        if index not in occupied
    ]
    alerts.sort(key=lambda a: (a.expected_date, a.iata, a.direction == "To"))
    return AirportCheckResult(tuple(result), tuple(alerts))
