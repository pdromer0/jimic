"""A problem found in a personal file: where it is and why, never what the file says."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Problem:
    line: int | None
    """1-based line, or `None` when the problem has no place (a missing section)."""
    reason: str
    """Spanish text for Pedro, built only from schema names and fixed words."""
