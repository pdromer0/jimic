"""The state of the day: what the heartbeat's decision gets, in compact JSON (1.3 and 6.3).

It gathers the local time, today's events, today's and overdue tasks, the APEX snapshot, the
proactive messages already sent, the messages left (the slot of the day close apart), the
active intentions and what waits in the group window. Each source says whether it is available,
with its code (decision 1.7-c): a source that is down appears only as that, never as empty
lists that would look like data.

Each active key task carries what the code knows of it (`key_task`, task 1.15): done and by what
source, no longer today's, «hoy no», the pause of [Voy ahora], from when it may insist, the level
of the tone and its notices of today (`jimic.heartbeat.key_tasks`).

Each active intention says whether it is due now and why (`due_now` and `why`, decision 1.15b-a):
the code compares the hours, so the model gets no window, default time nor `insist_from` to
compare (`jimic.heartbeat.due`).

The titles of tasks and events and the texts of the intentions go to the model; the `repr`
hides them, so a log or a traceback never shows them.
"""

import json
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from jimic.heartbeat.due import DueWhy, due_now
from jimic.heartbeat.key_tasks import KeyTaskStatus, key_task_status
from jimic.heartbeat.limits import Quota, WaitingNotices, applies_on, cap_per_day
from jimic.memory.key_marks import DayMarks
from jimic.memory.notices import SentToday
from jimic.memory.schemas import Intentions, KeyTask, Limits
from jimic.tools.apex_schema import ApexSnapshot
from jimic.tools.calendar import CalendarDay
from jimic.tools.todoist import TaskList

AVAILABILITY = {"available", "unavailable_reason"}


class SentEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    intent: str
    at: str
    """`HH:MM`, local."""


class ActiveIntention(BaseModel):
    """An intention that applies today, with how many times it may still go out and whether it
    is due now (task 1.15-b)."""

    model_config = ConfigDict(frozen=True)

    id: str
    type: str
    purpose: str = Field(repr=False)
    priority: str
    habit: str | None = None
    left_today: int
    due_now: bool
    why: DueWhy


class DayState(BaseModel):
    model_config = ConfigDict(frozen=True)

    now: datetime
    """Aware, in the zone of the settings."""
    calendar: CalendarDay
    tasks: TaskList
    apex: ApexSnapshot
    sent_today: list[SentEntry]
    messages_left: int
    """Messages left today for anything but the day close, the one of what waits taken."""
    close_slot_kept: bool
    """The last message is kept for the day close (decision 1.7-a)."""
    intentions: list[ActiveIntention]
    waiting: list[str]
    """The ids of the notices waiting in the group window (their text stays in the queue)."""
    key_tasks: dict[str, KeyTaskStatus] = Field(default_factory=dict, repr=False)
    """What the code knows of each active key task, by intention (task 1.15)."""

    @property
    def due(self) -> frozenset[str]:
        """The ids of the intentions due now (task 1.15-b)."""
        return frozenset(intention.id for intention in self.intentions if intention.due_now)

    @property
    def may_pick(self) -> frozenset[str]:
        """What the second pass lets the model pick (task 1.15-b): what is due, and what is due
        but for the room, which the quota of the second pass blocks with its own code
        (`close_slot`, `daily_cap`)."""
        return frozenset(
            intention.id
            for intention in self.intentions
            if intention.due_now or intention.why == "no_room"
        )

    def sources(self) -> dict[str, dict[str, Any]]:
        return {
            name: source.model_dump(include=AVAILABILITY, exclude_none=True)
            for name, source in (
                ("calendar", self.calendar),
                ("tasks", self.tasks),
                ("apex", self.apex),
            )
        }

    def to_json(self) -> str:
        """The compact JSON the model gets. A source that is down gives only its code."""
        document: dict[str, Any] = {
            "now": self.now.isoformat(timespec="minutes"),
            "sources": self.sources(),
        }
        if self.calendar.available:
            document["events"] = [
                event.model_dump(mode="json", exclude_none=True) for event in self.calendar.events
            ]
        if self.tasks.available:
            document["tasks"] = [
                task.model_dump(mode="json", exclude_none=True) for task in self.tasks.tasks
            ]
        if self.apex.available:
            document["apex"] = self.apex.model_dump(
                mode="json", exclude_none=True, exclude=AVAILABILITY
            )
        document |= {
            "sent_today": [entry.model_dump() for entry in self.sent_today],
            "messages_left": self.messages_left,
            "close_slot_kept": self.close_slot_kept,
            "intentions": [self._intention(intention) for intention in self.intentions],
            "waiting": self.waiting,
        }
        return json.dumps(document, ensure_ascii=False, separators=(",", ":"))

    def _intention(self, intention: ActiveIntention) -> dict[str, Any]:
        entry = intention.model_dump(exclude_none=True)
        status = self.key_tasks.get(intention.id)
        if status is not None:
            entry["key_task"] = status.to_json(self.now)
        return entry


def _hour(moment: datetime | time) -> str:
    return moment.strftime("%H:%M")


def build_day_state(
    now: datetime,
    timezone: ZoneInfo,
    calendar: CalendarDay,
    tasks: TaskList,
    apex: ApexSnapshot,
    sent: SentToday,
    intentions: Intentions,
    limits: Limits,
    queue: WaitingNotices,
    *,
    completed: TaskList | None = None,
    marks: DayMarks | None = None,
    jumped: frozenset[str] = frozenset(),
) -> DayState:
    """The state of the day of `now`, in `timezone`. `completed` are the tasks completed today
    (not read: unavailable), `marks` those of `key_task_marks` today and `jumped` the ids of the
    tasks seen today that are now open on a later day (task 1.15)."""
    now = now.astimezone(timezone)
    completed = completed if completed is not None else TaskList.unavailable("not_read")
    marks = marks if marks is not None else DayMarks()
    habits = {habit.id: habit for habit in intentions.habits}
    key_tasks: dict[str, KeyTaskStatus] = {}
    quota = Quota.of(limits, intentions, sent)
    waiting = [item.intent for item in queue.items]
    # What waits goes out as one message, so it already holds one of them.
    messages_left = max(0, quota.left_for_others - (1 if waiting else 0))
    close_fits = quota.refusal(is_close=True) is None
    active = []
    for intention in intentions.intentions:
        if not applies_on(intention, now.date()):
            continue
        if isinstance(intention, KeyTask):
            key_tasks[intention.id] = key_task_status(
                now=now,
                intention=intention,
                habit=habits.get(intention.habit),
                tasks=tasks,
                completed=completed,
                apex=apex,
                marks=marks.of(intention.id),
                jumped=jumped,
                moved=marks.moved,
                sent_today=sent.sent_count(intention.id),
            )
        left_today = max(
            0,
            cap_per_day(intention, limits) - sent.count(intention.id) - waiting.count(intention.id),
        )
        due = due_now(
            intention,
            now,
            left_today=left_today,
            waiting=intention.id in waiting,
            key=key_tasks.get(intention.id),
            messages_left=messages_left,
            close_fits=close_fits,
        )
        active.append(
            ActiveIntention(
                id=intention.id,
                type=intention.type,
                purpose=intention.purpose,
                priority=intention.priority,
                habit=intention.habit if isinstance(intention, KeyTask) else None,
                left_today=left_today,
                due_now=due.due,
                why=due.why,
            )
        )
    return DayState(
        now=now,
        calendar=calendar,
        tasks=tasks,
        apex=apex,
        sent_today=[
            SentEntry(intent=notice.intent, at=_hour(notice.sent_at.astimezone(timezone)))
            for notice in sent.notices
        ],
        messages_left=messages_left,
        close_slot_kept=quota.close_slot_kept,
        intentions=active,
        waiting=waiting,
        key_tasks=key_tasks,
    )
