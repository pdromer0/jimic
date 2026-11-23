"""The context of [✏️] for the agent (decision 1.14-h).

[✏️ Cámbialo] (plan) and [✏️ Cambiar] (memory) resolve their item with «¿cómo la quieres?» and
wait for Pedro's answer, which goes to the agent like any message. So that the agent knows what
the answer is about, the code remembers the **last** [✏️] Pedro tapped (the item, with the same
label as the note of the follow-up, `item_label`) and puts one line of its own before his next
message: `(JimiC) Pedro tocó ✏️ en: crear «Gimnasio» (2030-01-10T12:00)`.

- It is used once, with the next message of Pedro that reaches the agent, and forgotten. A
  command (`/memoria`) or a written «sí» do not reach the agent: they do not use it.
- Only on the same local day as the tap; another day it is forgotten.
- If another tap closes that message (`closed`, from the follow-up hook of the channel), it is
  forgotten: the message is over. The tap of [✏️] itself may close it; that one keeps it.
- The line is not Pedro's: it never enters the origin of the run (`RunState.origin` is his text
  alone), so nothing of it can become a memory change.

It lives in the memory of the service: a restart forgets it, as Pedro would expect of a tap
older than the restart.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from zoneinfo import ZoneInfo

from jimic.channel.message import Item
from jimic.channel.taps import HandlerOutcome, TapHandler
from jimic.clock import Clock
from jimic.execution.follow_up import EDITS, FOLLOW_KINDS, item_label

logger = logging.getLogger(__name__)

LINE = "Pedro tocó ✏️ en: {label}"


@dataclass(frozen=True)
class _Edit:
    approval_id: int
    label: str
    day: date
    others: bool = False
    """Another item of the same message was tapped after the [✏️]."""


class EditContext:
    def __init__(self, clock: Clock, timezone: ZoneInfo) -> None:
        self._clock = clock
        self._timezone = timezone
        self._edit: _Edit | None = None

    def track(self, handlers: Mapping[str, TapHandler]) -> dict[str, TapHandler]:
        """`handlers` with those of the proposals (`plan`, `memory`) watched for [✏️]."""
        return {
            kind: self._watched(handler) if kind in FOLLOW_KINDS else handler
            for kind, handler in handlers.items()
        }

    def _watched(self, handler: TapHandler) -> TapHandler:
        async def watched(item: Item, option: str, approval_id: int) -> HandlerOutcome:
            outcome = await handler(item, option, approval_id)
            if option in EDITS and isinstance(outcome, str):
                day = self._clock.now().astimezone(self._timezone).date()
                self._edit = _Edit(approval_id, item_label(item.data or {}), day)
            elif self._edit is not None and self._edit.approval_id == approval_id:
                self._edit = replace(self._edit, others=True)
            return outcome

        return watched

    def closed(self, approval_id: int) -> None:
        """A tap closed approval `approval_id`: if it was not the [✏️], forget it."""
        if self._edit is not None and self._edit.approval_id == approval_id and self._edit.others:
            logger.info("edit context dropped (closed)")
            self._edit = None

    def take(self, day: date) -> str | None:
        """The line for Pedro's message of `day`, once; then it is forgotten."""
        edit, self._edit = self._edit, None
        if edit is None or edit.day != day:
            return None
        logger.info("edit context used")
        return LINE.format(label=edit.label)
