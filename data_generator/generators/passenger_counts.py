"""Passenger-count physics helpers used by operational generation and tests."""
from __future__ import annotations

from typing import Tuple


def conserved_load(onboard_arrival: int, alightings: int, boardings: int) -> int:
    """Return the only ordinary departure load permitted by the contract."""
    if onboard_arrival < 0 or alightings < 0 or boardings < 0 or alightings > onboard_arrival:
        raise ValueError("invalid ordinary passenger count")
    return onboard_arrival - alightings + boardings


def validate_ordinary_count(*, onboard_arrival: int, alightings: int, boardings: int,
                            onboard_departure: int, replacement_event: bool = False,
                            transfer_out_count: int = 0, transfer_in_count: int = 0) -> Tuple[bool, str]:
    try:
        expected = conserved_load(onboard_arrival, alightings, boardings)
    except ValueError as exc:
        return False, str(exc)
    if expected != onboard_departure:
        return False, "onboard_departure does not equal onboard_arrival - alightings + boardings"
    if not replacement_event and (transfer_out_count != 0 or transfer_in_count != 0):
        return False, "ordinary visits must have zero replacement transfer counters"
    if transfer_out_count < 0 or transfer_in_count < 0 or transfer_out_count != transfer_in_count:
        return False, "replacement transfer counters must be equal and non-negative"
    return True, "VALID"
