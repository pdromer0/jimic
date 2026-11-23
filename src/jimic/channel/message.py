"""The interactive message: items with their own row of buttons, a global row, and its state.

Everything here is pure (no Telegram, no SQLite, no clock), so it is tested with plain values.
The state of a message is the model itself: it is saved as JSON in `approvals.payload` and a tap
works after a restart because the model is rebuilt from it.

Rules of the format (task 1.4):

- Telegram HTML with **every** dynamic text escaped, never Markdown.
- `callback_data` is `<approval id>:<item number or g>:<option key>`: short ids, never content.
- A tap removes the row of what it resolved and says what happened in the text of the item.
- An item being executed is `running` (task 1.11): it has no row and no tap touches it, so a
  second tap never runs its effect twice. One that failed keeps its row with a `note`.
- The `key` style (task 1.15) is the message of the heartbeat with key tasks: its text is the
  body alone (the model's), and an item shows a line only once it has a status (`🏋️ Gimnasio —
  🏃 a por ello`). Its buttons go `KEY_ROW` to a row, without numbers; with more than one item,
  each label starts with the item's `icon`.
- At most `MAX_ITEMS` items, and the text always fits in `TELEGRAM_TEXT_LIMIT`: if it cannot,
  `ensure_fits` raises `MessageTooLong` instead of cutting; `fit_titles` shortens the titles
  shown (never what an item does) until it fits.
"""

import html
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, JsonValue, StringConstraints, model_validator

MAX_ITEMS = 8
MAX_OPTIONS = 4
"""Options per item: they share one row, and four labels fit in a phone's width."""
MAX_RESULT_CHARS = 40
MAX_LABEL_CHARS = 32
TELEGRAM_TEXT_LIMIT = 4096
MAX_APPROVAL_ID = 2**63 - 1
"""The largest `INTEGER PRIMARY KEY` of SQLite; it fixes the longest `callback_data` (30 bytes)."""
EXPIRED_RESULT = "caducado"
UNCONFIRMED_RESULT = "sin confirmar: mira Todoist"
"""What an item still running gets when its message expires: its effect may or may not have
happened, and it is never retried on its own."""
RUNNING_TEXT = "en curso…"
GLOBAL_TARGET = "g"
MIN_TITLE_CHARS = 20
"""`fit_titles` never shortens a title below this."""
KEY_ROW = 2
"""Buttons per row in the `key` style: two rows of two per key task (decision 1.15-c)."""

Key = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]{1,8}$")]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Short = Annotated[str, StringConstraints(min_length=1, max_length=MAX_RESULT_CHARS)]

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
    offers it, or `then` to what offers that one instead (task 1.14: [⏭ Nada] keeps a delete
    with its «Déjala»). An item that offers neither stays pending."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: Key
    label: Label
    option: Key
    then: Key | None = None

    def applies_to(self, item: Item) -> Key | None:
        """The option this button chooses on `item`, or `None` if it does not touch it."""
        if item.offers(self.option):
            return self.option
        if self.then is not None and item.offers(self.then):
            return self.then
        return None


class Item(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: Text
    options: tuple[Option, ...]
    result: Short | None = None
    """What happened, once resolved or expired; `None` while the item is pending."""
    chosen: Key | None = None
    """The key of the option that resolved it; `None` while pending or when it expired."""
    running: bool = False
    """Claimed by a tap whose effect is under way: no row, and no other tap touches it."""
    note: Short | None = None
    """Shown while still pending: why the last try failed, or that the task changed."""
    data: dict[str, JsonValue] | None = None
    """What the handler of its kind needs (ids, the snapshot); never shown."""
    icon: str | None = None
    """In the `key` style with several items, what goes before each of its buttons."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not 1 <= len(self.options) <= MAX_OPTIONS:
            raise ValueError(f"an item has between 1 and {MAX_OPTIONS} options")
        keys = [option.key for option in self.options]
        if len(set(keys)) != len(keys):
            raise ValueError("the options of an item must have different keys")
        if self.chosen is not None and (self.chosen not in keys or self.result is None):
            raise ValueError("`chosen` must be one of the options and come with a result")
        if self.running and self.result is not None:
            raise ValueError("a resolved item is not running")
        return self

    @property
    def pending(self) -> bool:
        """Waiting for a tap: not resolved and not running."""
        return self.result is None and not self.running

    @property
    def unresolved(self) -> bool:
        return self.result is None

    def offers(self, key: str) -> bool:
        return any(option.key == key for option in self.options)


type Style = Literal["list", "plan", "key"]


class InteractiveMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    header: Text | None = None
    """In bold, just above the items (in the `plan` style, plain and with no blank line)."""
    body: Text | None = None
    """Plain text before the header: the agent's answer (decision 1.11-a), or the top of the
    plan of the day (task 1.14)."""
    items: tuple[Item, ...]
    footer: Text | None = None
    """Plain text after the items (the plan of the day: what was left out, 🧠 and 🏋️)."""
    style: Style = "list"
    """`list` numbers the items `1.`; `plan` numbers them `1️⃣`, as the format of the plan; `key`
    does not number them and shows an item only once it has a status (task 1.15)."""
    global_options: tuple[GlobalOption, ...] = ()
    expired: bool = False
    expired_text: Short = EXPIRED_RESULT
    """What a pending item says once the message expires."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.header is None and self.body is None:
            raise ValueError("a message needs a header or a body")
        if not 1 <= len(self.items) <= MAX_ITEMS:
            raise ValueError(f"a message has between 1 and {MAX_ITEMS} items")
        keys = [option.key for option in self.global_options]
        if len(set(keys)) != len(keys):
            raise ValueError("the global options must have different keys")
        return self

    @property
    def pending_count(self) -> int:
        """Items not resolved yet, the running ones included."""
        return sum(item.unresolved for item in self.items)


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


def number_mark(number: int, style: Style) -> str:
    """`1.` in a list; the keycap `1️⃣` in the plan of the day."""
    return f"{number}." if style == "list" else f"{number}\ufe0f\u20e3"


def _text(message: InteractiveMessage) -> str:
    lines: list[str] = []
    if message.body is not None:
        lines += [_escape(message.body), ""]
    if message.header is not None:
        if message.style == "plan":
            lines.append(_escape(message.header))
        else:
            lines += [f"<b>{_escape(message.header)}</b>", ""]
    for number, item in enumerate(message.items, start=1):
        status = item.result or (RUNNING_TEXT if item.running else item.note)
        if message.style == "key":
            if status is not None:  # a pending item has no line: its buttons say it
                lines.append(f"{_escape(item.title)} — {_escape(status)}")
            continue
        line = f"{number_mark(number, message.style)} {_escape(item.title)}"
        if status is not None:
            line += f" — {_escape(status)}"
        lines.append(line)
    if message.style == "key" and lines and lines[-1] == "":
        lines.pop()  # nothing with a status yet: the body's blank line goes
    if message.footer is not None:
        lines += ["", _escape(message.footer)]
    return "\n".join(lines)


def _key_rows(message: InteractiveMessage, approval_id: int) -> list[tuple[Button, ...]]:
    """`KEY_ROW` buttons to a row; with several items, each label after its item's icon."""
    rows: list[tuple[Button, ...]] = []
    several = len(message.items) > 1
    for number, item in enumerate(message.items, start=1):
        if not item.pending:
            continue
        mark = (item.icon or number_mark(number, "plan")) if several else None
        buttons = [
            Button(
                option.label if mark is None else f"{mark} {option.label}",
                f"{approval_id}:{number}:{option.key}",
            )
            for option in item.options
        ]
        rows += [tuple(buttons[i : i + KEY_ROW]) for i in range(0, len(buttons), KEY_ROW)]
    return rows


def _keyboard(message: InteractiveMessage, approval_id: int) -> tuple[tuple[Button, ...], ...]:
    if message.expired:
        return ()
    if message.style == "key":
        rows = _key_rows(message, approval_id)
    else:
        rows = [
            tuple(
                Button(f"{number} {option.label}", f"{approval_id}:{number}:{option.key}")
                for option in item.options
            )
            for number, item in enumerate(message.items, start=1)
            if item.pending
        ]
    # A global button is shown only while something pending can take it.
    global_row = tuple(
        Button(option.label, f"{approval_id}:{GLOBAL_TARGET}:{option.key}")
        for option in message.global_options
        if any(item.pending and option.applies_to(item) for item in message.items)
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


def worst_length(message: InteractiveMessage) -> int:
    """The longest text the message can reach in any later state.

    The worst case is every item with a status of `&` (the one that grows most when escaped,
    5 times): a result, a note and «en curso…» are all at most `MAX_RESULT_CHARS`.
    """
    worst = message.model_copy(
        update={
            "items": tuple(
                item.model_copy(update={"result": "&" * MAX_RESULT_CHARS, "running": False})
                for item in message.items
            )
        }
    )
    return max(telegram_length(_text(message)), telegram_length(_text(worst)))


def ensure_fits(message: InteractiveMessage) -> None:
    """Raise `MessageTooLong` unless every state the message can reach fits in one message."""
    length = worst_length(message)
    if length > TELEGRAM_TEXT_LIMIT:
        raise MessageTooLong(f"the message needs {length} characters and Telegram allows 4096")


def _shorten(title: str, length: int) -> str:
    return title[: length - 1].rstrip() + "…"


def fit_titles(message: InteractiveMessage) -> InteractiveMessage:
    """`message` with the longest titles shown cut with `…` until every state fits. Only the
    text shown changes: what an item does lives in its `data`. Raises `MessageTooLong` if it
    cannot fit even with every title at `MIN_TITLE_CHARS`."""
    current = message
    while worst_length(current) > TELEGRAM_TEXT_LIMIT:
        index, item = max(enumerate(current.items), key=lambda pair: len(pair[1].title))
        if len(item.title) <= MIN_TITLE_CHARS:
            ensure_fits(current)  # raises: nothing left to cut
        cut = max(MIN_TITLE_CHARS, len(item.title) * 9 // 10)
        items = list(current.items)
        items[index] = item.model_copy(update={"title": _shorten(item.title, cut)})
        current = current.model_copy(update={"items": tuple(items)})
    return current


def parse_callback(data: str) -> Tap | None:
    """The tap that `data` says, or `None` if it is not one of ours."""
    match = CALLBACK_DATA.fullmatch(data)
    if match is None or int(match[1]) > MAX_APPROVAL_ID:
        return None
    target = match[2]
    return Tap(int(match[1]), None if target == GLOBAL_TARGET else int(target), match[3])


def clip_result(result: str) -> str:
    """A result or a note as one line of at most `MAX_RESULT_CHARS` characters, cut with `…`."""
    line = " ".join(result.split()) or "hecho"
    if len(line) <= MAX_RESULT_CHARS:
        return line
    return line[: MAX_RESULT_CHARS - 1] + "…"


def targets(message: InteractiveMessage, tap: Tap) -> list[tuple[int, str]]:
    """The pending items a tap touches, as `(index from 0, option key)`.

    An item tap touches that item if it is pending and offers the option. A global tap touches
    every pending item the global button applies to, with the option it chooses there.
    Anything else, none.
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
    touched = []
    for index, item in enumerate(message.items):
        option = chosen.applies_to(item) if item.pending else None
        if option is not None:
            touched.append((index, option))
    return touched


def resolve(message: InteractiveMessage, outcomes: Mapping[int, Outcome]) -> InteractiveMessage:
    """The message with those items resolved (index from 0), running or not; what was resolved
    is not touched."""
    items = tuple(
        item.model_copy(
            update={
                "result": clip_result(outcomes[index].result),
                "chosen": outcomes[index].option,
                "running": False,
                "note": None,
            }
        )
        if index in outcomes and item.unresolved
        else item
        for index, item in enumerate(message.items)
    )
    return message.model_copy(update={"items": items})


def claim(message: InteractiveMessage, indexes: Collection[int]) -> InteractiveMessage:
    """The message with those pending items marked `running` (their rows go)."""
    items = tuple(
        item.model_copy(update={"running": True}) if index in indexes and item.pending else item
        for index, item in enumerate(message.items)
    )
    return message.model_copy(update={"items": items})


def release(message: InteractiveMessage, index: int, item: Item) -> InteractiveMessage:
    """The message with the unresolved item at `index` replaced by `item`, pending again: a
    try that failed (with its `note`) or a task that changed (renewed). The titles are fitted
    again, since a renewed one may be longer; if even that cannot fit, the item keeps the title
    it had (which fitted). It never raises: it runs after the effects of a tap."""
    old = message.items[index]
    if not old.unresolved:
        return message
    pending = {"running": False, "result": None, "chosen": None}
    items = list(message.items)
    items[index] = item.model_copy(update=pending)
    try:
        return fit_titles(message.model_copy(update={"items": tuple(items)}))
    except MessageTooLong:
        items[index] = item.model_copy(update=pending | {"title": old.title})
        return message.model_copy(update={"items": tuple(items)})


def expire(message: InteractiveMessage) -> InteractiveMessage:
    """The message after its time ran out: what was pending says its `expired_text`, what was
    running says it is unconfirmed; no keyboard."""
    items = tuple(
        item.model_copy(
            update={
                "result": UNCONFIRMED_RESULT if item.running else message.expired_text,
                "running": False,
            }
        )
        if item.unresolved
        else item
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


def reply_chunks(text: str) -> list[str]:
    """`text` escaped for Telegram HTML, in as few messages as fit in `TELEGRAM_TEXT_LIMIT`.

    It is cut between lines when it can, and a line too long for one message between characters;
    never inside an entity (`&amp;`), because each piece is escaped whole.
    """
    chunks: list[str] = []
    current = ""
    for line in text.strip().splitlines(keepends=True):
        escaped = html.escape(line, quote=False)
        if telegram_length(current + escaped) <= TELEGRAM_TEXT_LIMIT:
            current += escaped
            continue
        if current:
            chunks.append(current)
            current = ""
        for character in line:
            piece = html.escape(character, quote=False)
            if telegram_length(current + piece) > TELEGRAM_TEXT_LIMIT:
                chunks.append(current)
                current = ""
            current += piece
    if current.strip():
        chunks.append(current)
    return [chunk.strip() for chunk in chunks if chunk.strip()]
