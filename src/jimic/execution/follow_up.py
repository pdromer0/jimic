"""The conversation goes on after Pedro's taps (decisions 1.13-c and 1.13-d).

When a tap closes a message (nothing pending any more), the code may run the agent once more in
today's session with a note it writes itself, `(JimiC) Pedro ha resuelto tu propuesta: …`, with
what happened to each item. That run has no origin (no memory proposals, nothing at once), is
not «Pedro conversa», spends from the same budget and waits for the lock of the conversation.

It happens only if **every** condition holds (`follow_up_note`):

- the message is a proposal of the agent (`plan` or `memory`) and nothing in it is pending (an
  item that failed stays pending, so it does not close);
- at least one item asked for it when it was proposed (`follow_up`);
- no item was resolved with [✏️ Cambiar] or [✏️ Cámbialo]: they already ask Pedro how he wants it,
  and his answer goes on with the conversation;
- Pedro did not write after the message was saved: his message already moved the conversation on
  (checked by `Conversation.run_note`, inside the lock).

It is once per message because only the tap that closes it can trigger it (`TapResult.closed`).
"""

from typing import Any

from jimic.channel.message import InteractiveMessage, Item
from jimic.execution.memory_message import EDIT as EDIT_CHANGE
from jimic.execution.memory_message import MEMORY_KIND, OPERATIONS
from jimic.execution.plan_message import EDIT as EDIT_CREATE
from jimic.execution.plan_message import PLAN_KIND
from jimic.plan.day import clip

FOLLOW_KINDS = frozenset({PLAN_KIND, MEMORY_KIND})
EDITS = frozenset({EDIT_CREATE.key, EDIT_CHANGE.key})
"""The keys of [✏️ Cámbialo] (plan) and [✏️ Cambiar] (memory), from their options."""
NOTE = (
    "Pedro ha resuelto tu propuesta con sus botones: {parts}. Sigue la conversación donde la"
    " dejaste; si no hay nada más que decir, contesta muy corto."
)


def item_label(data: dict[str, Any]) -> str:
    """What an item proposed, for the agent: the task and the action, or the file and the
    operation, or the idea."""
    action = data.get("action")
    if action == "create":
        return f"crear «{clip(str(data['title']))}» ({data['due']})"
    if action == "move":
        return f"mover «{clip(str(data['snapshot']['title']))}» a {data['due']}"
    if action == "delete":
        return f"borrar «{clip(str(data['snapshot']['title']))}»"
    if action == "change":
        return f"{data['file']} · {OPERATIONS.get(str(data['operation']), data['operation'])}"
    if action == "idea":
        return f"idea «{clip(str(data['text']))}»"
    return "propuesta"


def _part(item: Item) -> str:
    return f"{item_label(item.data or {})} — {item.result}"


def follow_up_note(kind: str, message: InteractiveMessage) -> str | None:
    """The note of the follow-up, or `None` if this message does not get one."""
    if kind not in FOLLOW_KINDS or message.pending_count:
        return None
    if not any((item.data or {}).get("follow_up") is True for item in message.items):
        return None
    if any(item.chosen in EDITS for item in message.items):
        return None
    return NOTE.format(parts="; ".join(_part(item) for item in message.items))
