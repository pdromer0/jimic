"""Writing a private file so that a crash leaves the old content or the new one, never half."""

import os
import tempfile
from pathlib import Path


def write_private(path: Path, text: str) -> None:
    """Replace `path` with `text` atomically and with mode 0600.

    The text goes to a temporary file in the same folder (`mkstemp` creates it 0600), to disk,
    and only then does `path` point at it.
    """
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")  # 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())  # the data is on disk before the name points at it
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)  # and so is the new name: a crash leaves the old file or the new one
    finally:
        os.close(directory)
