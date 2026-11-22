"""The configuration of the two YAML personal files as the service uses it, kept up to date.

`LiveConfig` holds the last good `Intentions` and `Limits`. Whoever needs them (the heartbeat,
its decider, the conversation, the executor) reads them at the moment of use, never a copy taken
at start, so a change reaches all of them at once. `LiveConfig.fixed` is a configuration that
never changes (the tests, the demo).
"""

from jimic.memory.schemas import Intentions, Limits


class LiveConfig:
    def __init__(self, intentions: Intentions, limits: Limits) -> None:
        self._intentions = intentions
        self._limits = limits

    @classmethod
    def fixed(cls, intentions: Intentions, limits: Limits) -> LiveConfig:
        return cls(intentions, limits)

    @property
    def intentions(self) -> Intentions:
        return self._intentions

    @property
    def limits(self) -> Limits:
        return self._limits
