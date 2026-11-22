"""The three operations of a memory change on the text of a file, as an exact patch (task 1.12).

A change never rewrites a file: it replaces one fragment, so everything else stays the same, byte
for byte (the comments of a YAML file too). The patch says where (`position`, in characters),
what it took out (`removed`) and what it put in (`inserted`), so undoing it is the same patch
the other way round.

- `add` puts `after` at the end of the file, on a line of its own.
- `replace` and `remove` need `before` to appear **exactly once**; `remove` of whole lines also
  takes their line break, so no blank line is left behind.
- What goes in, and the «antes» looked for, use the line break of the file (`\\r\\n` if it
  has one).
"""

from dataclasses import dataclass
from typing import Literal

type Operation = Literal["add", "replace", "remove"]


class EditError(ValueError):
    """The operation does not fit the text: `code` is `not_found` or `repeated`."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Patch:
    position: int
    removed: str
    inserted: str

    def apply(self, text: str) -> str:
        """`text` with the patch applied; `text` must have `removed` at `position`."""
        end = self.position + len(self.removed)
        if text[self.position : end] != self.removed:
            raise EditError("not_found")
        return text[: self.position] + self.inserted + text[end:]

    def inverse(self) -> Patch:
        """The patch that takes the result back to the text it came from."""
        return Patch(self.position, self.inserted, self.removed)


def newline_of(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _with_newline(fragment: str, newline: str) -> str:
    return fragment.replace("\r\n", "\n").replace("\n", newline)


def find_once(text: str, fragment: str) -> int:
    """Where `fragment` is in `text`; `EditError` unless it is there exactly once (overlapping
    appearances count: `aa` is twice in `aaa`)."""
    first = text.find(fragment)
    if first == -1:
        raise EditError("not_found")
    if text.find(fragment, first + 1) != -1:
        raise EditError("repeated")
    return first


def patch_for(text: str, operation: Operation, before: str | None, after: str | None) -> Patch:
    """The patch of `operation` on `text`. `before` and `after` must fit the operation (the
    model of the proposal checks it): `add` takes only `after`, `remove` only `before`."""
    newline = newline_of(text)
    if operation == "add":
        assert after is not None
        separator = "" if text == "" or text.endswith("\n") else newline
        inserted = _with_newline(after, newline)
        if not inserted.endswith("\n"):
            inserted += newline
        return Patch(len(text), "", separator + inserted)
    assert before is not None
    # The model copies the fragment from its prompt with `\n`: in a CRLF file it is `\r\n`.
    before = _with_newline(before, newline)
    position = find_once(text, before)
    if operation == "replace":
        assert after is not None
        return Patch(position, before, _with_newline(after, newline))
    removed = before
    end = position + len(before)
    whole_lines = (position == 0 or text[position - 1] == "\n") and not before.endswith("\n")
    if whole_lines:
        for line_break in ("\r\n", "\n"):
            if text.startswith(line_break, end):
                removed += line_break
                break
    return Patch(position, removed, "")
