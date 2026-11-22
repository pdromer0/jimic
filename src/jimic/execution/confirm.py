"""The written «sí» of a deletion (H1-1), read by the code before the agent sees the text.

A clear yes is the whole message, in lower case and without accents: `si`, `vale`, `borrala` or
`ok` (`sí.` or `sí, bórrala` go to the agent). It confirms a deletion only in one of two cases
(decision 1.11-d), and only while that deletion is pending and has not expired:

- (a) the last message JimiC sent, of any kind (a proposal, an answer, a notice of the
  heartbeat), is the one of that deletion: a «vale» to «¿vas al gimnasio?» deletes nothing;
- (b) Pedro answered that message with Telegram's «Responder», even if others came after.

Either way, that message must have **one** pending deletion: with two, only the buttons count
(one task per approval, H1-2). (a) lives in the channel's memory and does not survive a
restart; (b) is found by the id of the message, so it does.
"""

import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime

from jimic.channel import store
from jimic.channel.bot import Incoming
from jimic.execution.plan_message import PLAN_KIND

YES = frozenset({"si", "vale", "borrala", "ok"})


def normalized(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.strip().lower())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def is_yes(text: str) -> bool:
    return normalized(text) in YES


@dataclass(frozen=True)
class Target:
    approval_id: int
    item: int
    """Counting from 1, as a tap."""


def pending_deletion(
    conn: sqlite3.Connection, now: datetime, telegram_message_id: int
) -> Target | None:
    """The one pending deletion of the proposal shown in that message, or `None`."""
    try:
        approval = store.by_message(conn, telegram_message_id)
    except ValueError:
        return None
    if approval is None or approval.kind != PLAN_KIND or approval.status != "pending":
        return None
    if now >= approval.expires_at:
        return None
    deletions = [
        number
        for number, item in enumerate(approval.message.items, start=1)
        if item.pending and (item.data or {}).get("action") == "delete"
    ]
    if len(deletions) != 1:
        return None
    return Target(approval.id, deletions[0])


def target(conn: sqlite3.Connection, now: datetime, incoming: Incoming) -> Target | None:
    """The deletion this text confirms, or `None` (then the text goes to the agent)."""
    if not is_yes(incoming.text):
        return None
    if incoming.reply_to is not None:
        return pending_deletion(conn, now, incoming.reply_to)
    if incoming.last_sent is not None:
        return pending_deletion(conn, now, incoming.last_sent)
    return None
