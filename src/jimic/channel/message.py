"""The interactive message: items with their own row of buttons, a global row, and its state.

Everything here is pure (no Telegram, no SQLite, no clock), so it is tested with plain values.
The state of a message is the model itself: it is saved as JSON in `approvals.payload` and a tap
works after a restart because the model is rebuilt from it.

Rules of the format (task 1.4):

- Telegram HTML with **every** dynamic text escaped, never Markdown.
- `callback_data` is `<approval id>:<item number or g>:<option key>`: short ids, never content.
- A tap removes the row of what it resolved and says what happened in the text of the item.
- At most `MAX_ITEMS` items, and the text always fits in `TELEGRAM_TEXT_LIMIT`: if it cannot,
  `ensure_fits` raises `MessageTooLong` instead of cutting.
"""

import html
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

MAX_ITEMS = 8
MAX_OPTIONS = 4
"""Options per item: they share one row, and four labels fit in a phone's width."""
MAX_RESULT_CHARS = 40
MAX_LABEL_CHARS = 32
TELEGRAM_TEXT_LIMIT = 4096
MAX_APPROVAL_ID = 2**63 - 1
"""The largest `INTEGER PRIMARY KEY` of SQLite; it fixes the longest `callback_data` (30 bytes)."""
EXPIRED_RESULT = "caducado"
GLOBAL_TARGET = "g"

Key = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]{1,8}$")]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

CALLBACK_DATA = re.compile(r"([1-9][0-9]{0,18}):([1-8]|g):([a-z0-9]{1,8})")

type FinalStatus = Literal["approved", "rejected", "expired"]


class MessageError(ValueError):
    """A message that cannot be built or shown."""


class MessageTooLong(MessageError):
    """The message would not fit in one Telegram message, even in its worst case."""


class Option(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: Key
    label: Label
    approves: bool = True
    """Whether choosing it is a yes: it decides `approved` or `rejected` in `approvals`."""


class GlobalOption(BaseModel):
    """A button of the global row: it applies the option `option` to everything pending that
    offers it. An item that does not offer it stays pending (a delete is never included)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: Key
    label: Label
    option: Key


class Item(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: Text
    options: tuple[Option, ...]
    result: Annotated[str, StringConstraints(min_length=1, max_length=MAX_RESULT_CHARS)] | None = (
        None
    )
    """What happened, once resolved or expired; `None` while the item is pending."""
    chosen: Key | None = None
    """The key of the option that resolved it; `None` while pending or when it expired."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not 1 <= len(self.options) <= MAX_OPTIONS:
            raise ValueError(f"an item has between 1 and {MAX_OPTIONS} options")
        keys = [option.key for option in self.options]
        if len(set(keys)) != len(keys):
            raise ValueError("the options of an item must have different keys")
        if self.chosen is not None and (self.chosen not in keys or self.result is None):
            raise ValueError("`chosen` must be one of the options and come with a result")
        return self

    @property
    def pending(self) -> bool:
        return self.result is None

    def offers(self, key: str) -> bool:
        return any(option.key == key for option in self.options)


class InteractiveMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    header: Text
    items: tuple[Item, ...]
    global_options: tuple[GlobalOption, ...] = ()
    expired: bool = False

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not 1 <= len(self.items) <= MAX_ITEMS:
            raise ValueError(f"a message has between 1 and {MAX_ITEMS} items")
        keys = [option.key for option in self.global_options]
        if len(set(keys)) != len(keys):
            raise ValueError("the global options must have different keys")
        return self

    @property
    def pending_count(self) -> int:
        return sum(item.pending for item in self.items)


@dataclass(frozen=True)
class Button:
    label: str
    data: str
    """The `callback_data`: ids only, at most 64 bytes."""


@dataclass(frozen=True)
class View:
    """What Telegram shows: the HTML text and the rows of the inline keyboard."""

    text: str
    keyboard: tuple[tuple[Button, ...], ...]


@dataclass(frozen=True)
class Tap:
    approval_id: int
    item: int | None
    """The item, counting from 1; `None` for the global row."""
    option: str


@dataclass(frozen=True)
class Outcome:
    """What an item resolved with: the option chosen and the text that says what happened."""

    option: str
    result: str


def _escape(text: str) -> str:
    # Telegram HTML only needs these three; `quote=False` keeps `"` and `'` as they are.
    return html.escape(text, quote=False)


def _text(message: InteractiveMessage) -> str:
    lines = [f"<b>{_escape(message.header)}</b>", ""]
    for number, item in enumerate(message.items, start=1):
        line = f"{number}. {_escape(item.title)}"
        if item.result is not None:
            line += f" — {_escape(item.result)}"
        lines.append(line)
    return "\n".join(lines)


def _keyboard(message: InteractiveMessage, approval_id: int) -> tuple[tuple[Button, ...], ...]:
    if message.expired:
        return ()
    rows = [
        tuple(
            Button(f"{number} · {option.label}", f"{approval_id}:{number}:{option.key}")
            for option in item.options
        )
        for number, item in enumerate(message.items, start=1)
        if item.pending
    ]
    # A global button is shown only while something pending can take it.
    global_row = tuple(
        Button(option.label, f"{approval_id}:{GLOBAL_TARGET}:{option.key}")
        for option in message.global_options
        if any(item.pending and item.offers(option.option) for item in message.items)
    )
    if global_row:
        rows.append(global_row)
    return tuple(rows)


def render(message: InteractiveMessage, approval_id: int) -> View:
    """The text and the keyboard of `message`: one numbered row per pending item and, while
    something can take it, the global row. Nothing pending, nothing to press: no keyboard."""
    if not 1 <= approval_id <= MAX_APPROVAL_ID:
        raise MessageError("the approval id is out of range")
    return View(_text(message), _keyboard(message, approval_id))


def telegram_length(text: str) -> int:
    """Length in UTF-16 code units: an emoji outside the basic plane counts 2."""
    return len(text.encode("utf-16-le")) // 2


def ensure_fits(message: InteractiveMessage) -> None:
    """Raise `MessageTooLong` unless every state the message can reach fits in one message.

    The worst case is every item resolved with a result of `&` (the one that grows most when
    escaped, 5 times), so no later tap can push the text past the limit.
    """
    worst = message.model_copy(
        update={
            "items": tuple(
                item.model_copy(update={"result": "&" * MAX_RESULT_CHARS}) for item in message.items
            )
        }
    )
    length = max(telegram_length(_text(message)), telegram_length(_text(worst)))
    if length > TELEGRAM_TEXT_LIMIT:
        raise MessageTooLong(f"the message needs {length} characters and Telegram allows 4096")


def parse_callback(data: str) -> Tap | None:
    """The tap that `data` says, or `None` if it is not one of ours."""
    match = CALLBACK_DATA.fullmatch(data)
    if match is None or int(match[1]) > MAX_APPROVAL_ID:
        return None
    target = match[2]
    return Tap(int(match[1]), None if target == GLOBAL_TARGET else int(target), match[3])


def clip_result(result: str) -> str:
    """A result as one line of at most `MAX_RESULT_CHARS` characters, cut with `…`."""
    line = " ".join(result.split()) or "hecho"
    if len(line) <= MAX_RESULT_CHARS:
        return line
    return line[: MAX_RESULT_CHARS - 1] + "…"


def targets(message: InteractiveMessage, tap: Tap) -> list[tuple[int, str]]:
    """The pending items a tap touches, as `(index from 0, option key)`.

    An item tap touches that item if it is pending and offers the option. A global tap touches
    every pending item that offers the option the global button applies. Anything else, none.
    """
    if message.expired:
        return []
    if tap.item is not None:
        if tap.item > len(message.items):
            return []
        item = message.items[tap.item - 1]
        return [(tap.item - 1, tap.option)] if item.pending and item.offers(tap.option) else []
    chosen = next((o for o in message.global_options if o.key == tap.option), None)
    if chosen is None:
        return []
    return [
        (index, chosen.option)
        for index, item in enumerate(message.items)
        if item.pending and item.offers(chosen.option)
    ]


def resolve(message: InteractiveMessage, outcomes: Mapping[int, Outcome]) -> InteractiveMessage:
    """The message with those items resolved (index from 0); what was resolved is not touched."""
    items = tuple(
        item.model_copy(
            update={"result": clip_result(outcomes[index].result), "chosen": outcomes[index].option}
        )
        if index in outcomes and item.pending
        else item
        for index, item in enumerate(message.items)
    )
    return message.model_copy(update={"items": items})


def expire(message: InteractiveMessage) -> InteractiveMessage:
    """The message after its time ran out: what was pending says `caducado`; no keyboard."""
    items = tuple(
        item.model_copy(update={"result": EXPIRED_RESULT}) if item.pending else item
        for item in message.items
    )
    return message.model_copy(update={"items": items, "expired": True})


def final_status(message: InteractiveMessage) -> FinalStatus | None:
    """The state `approvals` gets, or `None` while something is still pending.

    `approved` if any item was resolved with an option that approves, else `rejected`.
    """
    if message.expired:
        return "expired"
    if message.pending_count:
        return None
    approving = {
        (index, option.key)
        for index, item in enumerate(message.items)
        for option in item.options
        if option.approves
    }
    chosen = {(index, item.chosen) for index, item in enumerate(message.items)}
    return "approved" if approving & chosen else "rejected"
