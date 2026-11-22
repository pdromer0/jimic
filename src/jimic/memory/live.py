"""The configuration of the two YAML personal files as the service uses it, kept up to date.

`LiveConfig` holds the last good `Intentions` and `Limits`. Whoever needs them (the heartbeat,
its decider, the conversation, the executor) reads them at the moment of use, never a copy taken
at start, so a change reaches all of them at once. `LiveConfig.fixed` is a configuration that
never changes (the tests, the demo).

Without restarting (task 1.12):

- `reload` reads and validates both files again; the service calls it after a [Guardar] or a
  [Deshacer] of a YAML file;
- `refresh`, at the start of each heartbeat, reloads only if a file changed: its modification
  time, its size or its inode (an editor that saves by renaming).

A file that does not validate (or is missing, or cannot be read) leaves the last good version in
use, and its name goes to `invalid` until it is valid again: the heartbeat sends the system
notice `config_invalid` for it, once a day.
"""

import logging
import os
from pathlib import Path

from pydantic import BaseModel

from jimic.memory.personal import (
    INTENTIONS_FILE,
    LIMITS_FILE,
    YAML_SCHEMAS,
    load_personal,
    validate_yaml,
)
from jimic.memory.schemas import Intentions, Limits
from jimic.memory.yaml_io import YamlSyntaxError, parse_yaml

logger = logging.getLogger(__name__)

type Signature = tuple[tuple[int, int, int] | None, ...]


def signature(config_dir: Path) -> Signature:
    """`(mtime_ns, size, inode)` of each YAML file, `None` for one that is missing."""
    marks = []
    for name in YAML_SCHEMAS:
        try:
            stat = os.stat(config_dir / name)
        except OSError:
            marks.append(None)
            continue
        marks.append((stat.st_mtime_ns, stat.st_size, stat.st_ino))
    return tuple(marks)


def read_valid(config_dir: Path, name: str) -> BaseModel | None:
    """The model of a YAML file, or `None` if it is missing, unreadable or invalid."""
    try:
        doc = parse_yaml((config_dir / name).read_text(encoding="utf-8-sig"))
    except OSError, UnicodeDecodeError, YamlSyntaxError:
        return None
    if validate_yaml(name, doc):
        return None
    return YAML_SCHEMAS[name].model_validate(doc)


class LiveConfig:
    def __init__(
        self,
        intentions: Intentions,
        limits: Limits,
        config_dir: Path | None = None,
        read_as: Signature | None = None,
    ) -> None:
        """`read_as` is the signature of the files when `intentions` and `limits` were read;
        without it (they were read who knows when), the first `refresh` reads them again."""
        self._intentions = intentions
        self._limits = limits
        self._config_dir = config_dir
        self._signature = read_as
        self.invalid: tuple[str, ...] = ()
        """The YAML files whose last version did not validate, in table order."""

    @classmethod
    def fixed(cls, intentions: Intentions, limits: Limits) -> LiveConfig:
        return cls(intentions, limits)

    @classmethod
    def load(cls, config_dir: Path) -> LiveConfig:
        """Both files must be valid now (`PersonalFilesInvalid` otherwise), as `jimic run` asks."""
        # Taken before reading: an edit while loading is seen by the next `refresh`.
        read_as = signature(config_dir)
        intentions, limits = load_personal(config_dir)
        return cls(intentions, limits, config_dir, read_as)

    @property
    def intentions(self) -> Intentions:
        return self._intentions

    @property
    def limits(self) -> Limits:
        return self._limits

    def reload(self) -> tuple[str, ...]:
        """Read both files again; keep the last good version of the one that does not validate.
        Return the files that do not validate."""
        if self._config_dir is None:
            return ()
        self._signature = signature(self._config_dir)
        invalid = []
        intentions = read_valid(self._config_dir, INTENTIONS_FILE)
        if isinstance(intentions, Intentions):
            self._intentions = intentions
        else:
            invalid.append(INTENTIONS_FILE)
        limits = read_valid(self._config_dir, LIMITS_FILE)
        if isinstance(limits, Limits):
            self._limits = limits
        else:
            invalid.append(LIMITS_FILE)
        self.invalid = tuple(invalid)
        logger.info("personal config reloaded (%d invalid)", len(invalid))
        return self.invalid

    def refresh(self) -> tuple[str, ...]:
        """Reload if a file changed since the last read; return the files that do not
        validate (also when nothing changed: they are still invalid)."""
        if self._config_dir is not None and signature(self._config_dir) != self._signature:
            self.reload()
        return self.invalid
