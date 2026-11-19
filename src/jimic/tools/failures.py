"""What the tools that go out to the network share: a failure is a code, and it is logged once.

A code is a type of failure (`auth`, `timeout`, `http_503`...), never part of the content, so it
is safe in a log and in a snapshot. The service goes on without the source and says so when the
source stops being available, not on every read.
"""

import logging

MAX_PAGES = 10
"""Pages read from any paginated list before giving up with `too_many_pages`."""


class ToolFailure(Exception):
    """A read or a write failed; `code` says why and carries no content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class WarnOnce:
    """Log a `WARNING` when a source goes from available to unavailable, and again if it comes
    back and fails once more; a source that stays down is not repeated."""

    def __init__(self, logger: logging.Logger, source: str) -> None:
        self._logger = logger
        self._source = source
        self._failing = False

    def update(self, reason: str | None, hint: str = "") -> None:
        """`reason` is the code of the failure of this read, or `None` if it worked."""
        if reason is not None and not self._failing:
            suffix = f"; {hint}" if hint else ""
            self._logger.warning("%s unavailable (%s)%s", self._source, reason, suffix)
        self._failing = reason is not None
