"""The part of `apex_db.json` that jimic uses, and the small snapshot of the day built from it.

The contract is strict about the fields used and blind to the rest of the file: a field that
changes type breaks it, a field that jimic never reads can change freely. Dates are text
(`AAAA-MM-DD`) parsed with ASCII-only patterns. Nothing here reads the clock or a file.

The snapshot is what the model sees (never the whole file) and it holds personal data, so its
`repr` hides the workouts, goals and biometrics: logging a snapshot must not leak them.
"""

import math
import re
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, PlainValidator
from pydantic_core import PydanticCustomError

DATE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
SYNC = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2}):([0-9]{2})")
RECENT_DAYS = 7
"""The window of the recent workouts and of the biometric average: today and the 6 days before."""
IN_PROGRESS = "en curso"
"""The value of `estado` of the goals that count: it is what APEX writes."""
SECTIONS = ("meta", "plan", "entrenamientos", "objetivos", "biometricos")


def _parse_date(value: object) -> date:
    match = DATE.fullmatch(value) if isinstance(value, str) else None
    try:
        if match is not None:
            return date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        pass
    raise PydanticCustomError("date_format", "date must be YYYY-MM-DD")


def _parse_sync(value: object) -> datetime:
    """`AAAA-MM-DD HH:MM:SS` with no zone: it is read in the zone of the settings later."""
    match = SYNC.fullmatch(value) if isinstance(value, str) else None
    try:
        if match is not None:
            year, month, day, hour, minute, second = (int(part) for part in match.groups())
            return datetime(year, month, day, hour, minute, second)
    except ValueError:
        pass
    raise PydanticCustomError("sync_format", "sync must be YYYY-MM-DD HH:MM:SS")


def _parse_number(value: object) -> float:
    """An `int` or `float` that is finite as a `float` (`bool`, NaN, infinities and ints too big
    for a `float` are errors)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise PydanticCustomError("number_type", "must be a number")
    try:
        number = float(value)
    except OverflowError:
        number = math.inf
    if not math.isfinite(number):
        raise PydanticCustomError("number_not_finite", "must be a finite number")
    return number


ApexDate = Annotated[date, PlainValidator(_parse_date)]
SyncTime = Annotated[datetime, PlainValidator(_parse_sync)]
Number = Annotated[float, PlainValidator(_parse_number)]


class _Used(BaseModel):
    """Strict about what is declared and blind to the rest of the object."""

    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)


class Meta(_Used):
    ultima_actualizacion: SyncTime


class Plan(_Used):
    inicio: ApexDate
    fin: ApexDate
    estructura_semanal: dict[str, str]


class Workout(_Used):
    fecha: ApexDate
    tipo: str
    nombre: str
    duracion_min: Number


class Goal(_Used):
    id: str
    desc: str
    meta: str
    prioridad: int
    estado: str
    progreso_pct: Number | None = None


class Reading(_Used):
    fecha: ApexDate
    valor: Number


class ApexData(_Used):
    meta: Meta
    plan: Plan
    entrenamientos: list[Workout]
    objetivos: list[Goal]
    biometricos: dict[str, list[Reading]] = {}


class TodayWorkout(StrEnum):
    """Whether Pedro trained today, as far as APEX knows."""

    TRAINED = "trained"
    NOT_RECORDED = "not_recorded"
    """APEX synced today and has no workout for today."""
    UNKNOWN = "unknown"
    """APEX last synced before today began: it cannot say."""


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class RecentWorkout(_Out):
    day: date
    type: str
    name: str
    duration_min: float


class ActiveGoal(_Out):
    id: str
    description: str
    target: str
    priority: int
    status: str
    progress_pct: float | None = None


class BiometricSummary(_Out):
    last_date: date
    last_value: float
    average_7d: float | None
    """The mean of the readings in the last 7 days; `None` if there are none."""


class ApexSnapshot(_Out):
    """What jimic knows today from APEX. When `available` is false only the reason is set."""

    available: bool
    unavailable_reason: str | None = None
    """A code (`file_missing`, `invalid_format:objetivos`...), never part of the content."""
    last_sync: datetime | None = None
    """When APEX last synced, in the zone of the settings."""
    planned_today: str | None = None
    """The workout planned for today; `None` outside the plan's block (no plan)."""
    today_workout: TodayWorkout | None = None
    recent_workouts: list[RecentWorkout] = Field(default_factory=list, repr=False)
    goals: list[ActiveGoal] = Field(default_factory=list, repr=False)
    biometrics: dict[str, BiometricSummary] = Field(default_factory=dict, repr=False)

    @classmethod
    def unavailable(cls, reason: str) -> ApexSnapshot:
        return cls(available=False, unavailable_reason=reason)

    def to_json(self) -> str:
        """The compact JSON the model gets (no empty fields)."""
        return self.model_dump_json(exclude_none=True)


def build_snapshot(data: ApexData, now: datetime, timezone: ZoneInfo) -> ApexSnapshot:
    """The snapshot of the day of `now` (aware), in `timezone`."""
    today = now.astimezone(timezone).date()
    start_of_day = datetime.combine(today, time.min, tzinfo=timezone)
    last_sync = data.meta.ultima_actualizacion.replace(tzinfo=timezone)
    first_day = today - timedelta(days=RECENT_DAYS - 1)

    plan = data.plan
    planned = None
    if plan.inicio <= today <= plan.fin:
        # `weekday()` is 0 for Monday, which is how APEX numbers `estructura_semanal`.
        planned = plan.estructura_semanal.get(str(today.weekday()))

    if last_sync < start_of_day:
        today_workout = TodayWorkout.UNKNOWN
    elif any(workout.fecha == today for workout in data.entrenamientos):
        today_workout = TodayWorkout.TRAINED
    else:
        today_workout = TodayWorkout.NOT_RECORDED

    recent = sorted(
        (w for w in data.entrenamientos if first_day <= w.fecha <= today), key=lambda w: w.fecha
    )
    goals = sorted(
        (g for g in data.objetivos if g.estado == IN_PROGRESS), key=lambda g: g.prioridad
    )

    biometrics = {}
    for name, readings in data.biometricos.items():
        known = [r for r in readings if r.fecha <= today]
        if not known:
            continue
        # Reversed, so that of two readings of the same day the last one in the file wins.
        last = max(reversed(known), key=lambda r: r.fecha)
        window = [r.valor for r in known if r.fecha >= first_day]
        biometrics[name] = BiometricSummary(
            last_date=last.fecha,
            last_value=last.valor,
            average_7d=sum(window) / len(window) if window else None,
        )

    return ApexSnapshot(
        available=True,
        last_sync=last_sync,
        planned_today=planned,
        today_workout=today_workout,
        recent_workouts=[
            RecentWorkout(day=w.fecha, type=w.tipo, name=w.nombre, duration_min=w.duracion_min)
            for w in recent
        ],
        goals=[
            ActiveGoal(
                id=g.id,
                description=g.desc,
                target=g.meta,
                priority=g.prioridad,
                status=g.estado,
                progress_pct=g.progreso_pct,
            )
            for g in goals
        ],
        biometrics=biometrics,
    )
