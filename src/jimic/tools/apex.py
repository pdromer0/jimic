"""The APEX reader: read-only by design (decision H1-10).

`ApexReader` has one method, `read_today`, and no way to write: that is the guarantee, and a test
checks it. `LocalApexReader` reads `apex_db.json` from a local path (task 2.2 adds the Drive
one). It reads the whole file only when it changes (modification time and size), and it returns
the small snapshot of the day, never the file.

When APEX cannot be read the snapshot says so with a code and the service goes on. The code is
a type (`file_missing`, `invalid_json`, `invalid_format:objetivos`...), never part of the
content; it goes to the log once, when APEX stops being available.
"""

import json
import logging
import os
import stat
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from jimic.clock import Clock
from jimic.tools.apex_schema import SECTIONS, ApexData, ApexSnapshot, build_snapshot

logger = logging.getLogger(__name__)

MAX_BYTES = 10 * 1024 * 1024
"""The real file is ~220 KB; a bigger one is not APEX's, and is not read."""


class ApexReader(Protocol):
    """What the rest of jimic knows about APEX: it can be read, and nothing else."""

    def read_today(self) -> ApexSnapshot:
        """The snapshot of today. It does not raise: failures come back as `unavailable`."""
        ...


class _Unreadable(Exception):
    """The file cannot be read now; `code` says why. Not cached: it may be a lock that passes."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


type _Result = ApexData | str
"""What the content gave: the data, or the code of the failure."""


class LocalApexReader:
    """Reads `apex_db.json` from a local path. Opens it for reading only."""

    def __init__(self, path: Path, clock: Clock, timezone: ZoneInfo) -> None:
        self._path = path
        self._clock = clock
        self._timezone = timezone
        self._cached: tuple[tuple[int, int], _Result] | None = None
        self._failing = False

    def read_today(self) -> ApexSnapshot:
        snapshot = self._snapshot()
        reason = snapshot.unavailable_reason
        if reason is not None and not self._failing:
            logger.warning("APEX unavailable (%s)", reason)
        self._failing = reason is not None
        return snapshot

    def _snapshot(self) -> ApexSnapshot:
        try:
            result = self._result()
            if isinstance(result, str):
                return ApexSnapshot.unavailable(result)
            return build_snapshot(result, self._clock.now(), self._timezone)
        except _Unreadable as error:
            return ApexSnapshot.unavailable(error.code)
        except Exception as error:  # a bug here must not take the service down
            return ApexSnapshot.unavailable(f"unexpected:{type(error).__name__}")

    def _result(self) -> _Result:
        key = self._key(self._stat())
        if self._cached is not None and self._cached[0] == key:
            return self._cached[1]
        try:
            with self._path.open("rb") as file:
                key = self._key(os.fstat(file.fileno()))  # the key of what is read, not of before
                raw = file.read(MAX_BYTES + 1)
        except OSError:
            raise _Unreadable("file_unreadable") from None
        if len(raw) > MAX_BYTES:
            raise _Unreadable("file_too_large")
        result = self._parse(raw)
        self._cached = (key, result)
        return result

    def _stat(self) -> os.stat_result:
        try:
            return self._path.stat()
        except FileNotFoundError:
            raise _Unreadable("file_missing") from None
        except OSError:
            raise _Unreadable("file_unreadable") from None

    @staticmethod
    def _key(info: os.stat_result) -> tuple[int, int]:
        if not stat.S_ISREG(info.st_mode):
            raise _Unreadable("file_unreadable")  # a folder, or a pipe that would block
        if info.st_size > MAX_BYTES:
            raise _Unreadable("file_too_large")
        return info.st_mtime_ns, info.st_size

    @staticmethod
    def _parse(raw: bytes) -> _Result:
        try:
            document = json.loads(raw)
        except ValueError, RecursionError:  # bad JSON, bad UTF-8 or nested too deep
            return "invalid_json"
        try:
            return ApexData.model_validate(document)
        except ValidationError as error:
            first = error.errors(include_input=False, include_url=False, include_context=False)[0]
            # Only a section we know: the next names in `loc` can be Pedro's own keys.
            section = first["loc"][0] if first["loc"] else "root"
            return f"invalid_format:{section if section in SECTIONS else 'root'}"
