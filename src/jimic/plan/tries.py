"""How many times a hook of the heartbeat tried an intention today (task 1.14, Pedro's review of
the plan; the close reuses it, task 1.16).

A hook that raises leaves the intention unattended, so a later heartbeat hands it over again. The
count says which try this is, so the hook can give up the agent after the first retry and the
whole thing after one more: a failure of Telegram that lasts never runs the agent on every
heartbeat. Only today's counts are kept; they live in the service (a restart starts again).
"""

from datetime import date


class DailyTries:
    def __init__(self) -> None:
        self._tries: dict[tuple[date, str], int] = {}

    def take(self, day: date, intent: str) -> int:
        """Count one more try of `intent` on `day`; return how many there were before it."""
        key = (day, intent)
        before = self._tries.get(key, 0)
        self._tries = {k: v for k, v in self._tries.items() if k[0] == day} | {key: before + 1}
        return before
