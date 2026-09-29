"""Deterministic random substreams.

No generator component should use the process-global random state.  A substream
is derived from the master seed and a human-readable entity/date/scenario key,
so changing an unrelated table or output shard does not reshuffle operations.
"""
from __future__ import annotations

import hashlib
import random
from typing import Any, Iterable, Sequence, TypeVar

T = TypeVar("T")


def derive_seed(master_seed: int, *parts: Any) -> int:
    material = "|".join([str(master_seed), *(str(part) for part in parts)])
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def rng_for(master_seed: int, *parts: Any) -> random.Random:
    return random.Random(derive_seed(master_seed, *parts))


def stable_unit_interval(*parts: Any) -> float:
    """A deterministic [0, 1) value independent of Python hash randomization."""

    material = "|".join(str(part) for part in parts)
    return int(hashlib.sha256(material.encode("utf-8")).hexdigest()[:16], 16) / float(1 << 64)


def stable_choice(master_seed: int, values: Sequence[T], *parts: Any) -> T:
    if not values:
        raise ValueError("cannot choose from an empty sequence")
    return values[derive_seed(master_seed, *parts) % len(values)]


def stable_index(master_seed: int, upper: int, *parts: Any) -> int:
    if upper <= 0:
        raise ValueError("upper must be positive")
    return derive_seed(master_seed, *parts) % upper


def shuffled(values: Iterable[T], master_seed: int, *parts: Any) -> list[T]:
    """Return a deterministic shuffled copy of a small in-memory collection."""

    result = list(values)
    rng_for(master_seed, *parts).shuffle(result)
    return result
