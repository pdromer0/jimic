"""La regla asimétrica de `limites.yaml` (decisión 11): conversando, solo más discreto.

Un test por campo y sentido de la tabla de direcciones de las notas de la 1.12. Los valores de
`quiet_hours` son los valores a mano del plan (minutos por conjuntos, con código independiente).
"""

from typing import Any

import pytest
from heartbeat_data import LIMITS, limits

from jimic.memory.discreet import holds, judge, minutes
from jimic.memory.schemas import Budget, Window

BUDGET = LIMITS["budget"]


def budget(**changes: Any) -> dict[str, Any]:
    return {**BUDGET, **changes}


# (nombre, valor más discreto, valor que relaja), sobre los de `LIMITS`.
FIELDS = [
    ("max_messages_per_day", {"max_messages_per_day": 6}, {"max_messages_per_day": 15}),
    ("max_insist_per_task", {"max_insist_per_task": 2}, {"max_insist_per_task": 4}),
    ("max_deletions_per_day", {"max_deletions_per_day": 3}, {"max_deletions_per_day": 8}),
    ("group_window", {"group_window": "45m"}, {"group_window": "15m"}),
    ("quiet_while_chatting", {"quiet_while_chatting": "15m"}, {"quiet_while_chatting": "5m"}),
    (
        "budget.monthly_target_usd",
        {"budget": budget(monthly_target_usd=20.0)},
        {"budget": budget(monthly_target_usd=40.0)},
    ),
    ("budget.warn_at", {"budget": budget(warn_at=0.5)}, {"budget": budget(warn_at=0.9)}),
    (
        "budget.daily_cap_usd",
        {"budget": budget(daily_cap_usd=2.0)},
        {"budget": budget(daily_cap_usd=5.0)},
    ),
    (
        "budget.max_session_usd",
        {"budget": budget(max_session_usd=0.25)},
        {"budget": budget(max_session_usd=1.0)},
    ),
]


@pytest.mark.parametrize(("name", "discreet", "_"), FIELDS, ids=[f[0] for f in FIELDS])
def test_hacia_lo_discreto_se_acepta(name, discreet, _):
    verdict = judge(limits(), limits(**discreet))
    assert verdict.changed == (name,)
    assert verdict.accepted


@pytest.mark.parametrize(("name", "_", "relaxing"), FIELDS, ids=[f[0] for f in FIELDS])
def test_lo_que_relaja_se_rechaza_nombrando_el_campo(name, _, relaxing):
    verdict = judge(limits(), limits(**relaxing))
    assert verdict.relaxed == (name,)
    assert not verdict.accepted


def test_max_deletions_per_day_de_5_a_3_si_y_de_5_a_8_no():
    """El caso concreto de las notas."""
    assert judge(limits(), limits(max_deletions_per_day=3)).accepted
    assert judge(limits(), limits(max_deletions_per_day=8)).relaxed == ("max_deletions_per_day",)


def test_silent_during_events_solo_de_false_a_true():
    assert judge(limits(silent_during_events=False), limits(silent_during_events=True)).accepted
    assert judge(limits(), limits(silent_during_events=False)).relaxed == ("silent_during_events",)


def window(start: str, end: str) -> Window:
    return Window.model_validate({"from": start, "to": end})


@pytest.mark.parametrize(
    ("new", "size", "accepted"),
    [
        (("23:00", "08:00"), 540, True),
        (("22:00", "07:00"), 540, True),
        (("08:00", "08:00"), 1440, True),
        (("00:00", "07:00"), 420, False),
        (("06:00", "08:00"), 120, False),
        (("07:00", "23:00"), 960, False),
    ],
)
def test_quiet_hours_la_nueva_contiene_a_la_vieja(new, size, accepted):
    """La vieja es 23:00–07:00 (480 minutos); `from == to` es el día entero (decisión 80)."""
    old = window("23:00", "07:00")
    assert len(minutes(old)) == 480
    assert len(minutes(window(*new))) == size
    verdict = judge(limits(), limits(quiet_hours={"from": new[0], "to": new[1]}))
    assert verdict.changed == ("quiet_hours",)
    assert verdict.accepted is accepted


def test_quiet_hours_del_dia_entero_a_una_ventana_se_rechaza():
    assert not holds(window("23:00", "07:00"), window("07:00", "07:00"))


def test_quiet_hours_que_pasa_a_cruzar_la_medianoche():
    assert holds(window("23:00", "06:00"), window("01:00", "05:00"))


def test_quiet_hours_otro_dia_entero_no_mueve_nada():
    """`07:00–07:00` y `08:00–08:00` son el mismo silencio: no hay cambio de valor."""
    verdict = judge(
        limits(quiet_hours={"from": "07:00", "to": "07:00"}),
        limits(quiet_hours={"from": "08:00", "to": "08:00"}),
    )
    assert verdict.changed == ()


def levels(**changes: str) -> dict[str, str]:
    return {**LIMITS["tool_levels"], **changes}


def test_tool_levels_de_free_a_blocked_si_y_al_reves_no():
    blocked = limits(tool_levels=levels(log_idea="blocked"))
    assert judge(limits(), blocked).changed == ("tool_levels.log_idea",)
    assert judge(limits(), blocked).accepted
    assert judge(blocked, limits()).relaxed == ("tool_levels.log_idea",)


def test_tool_levels_quitar_una_libre_es_bloquearla():
    """La que falta cuenta como bloqueada, como en la guardia."""
    without = dict(LIMITS["tool_levels"])
    del without["read_tasks"]
    verdict = judge(limits(), limits(tool_levels=without))
    assert verdict.changed == ("tool_levels.read_tasks",) and verdict.accepted
    assert judge(limits(tool_levels=without), limits()).relaxed == ("tool_levels.read_tasks",)


def test_tool_levels_de_blocked_a_ausente_no_mueve_nada():
    blocked = levels(log_idea="blocked")
    absent = dict(blocked)
    del absent["log_idea"]
    assert judge(limits(tool_levels=blocked), limits(tool_levels=absent)).changed == ()


def test_sensitive_categories_cualquier_cambio_se_rechaza():
    """El esquema la quiere vacía hasta el H4; la regla la rechaza igual, por si un día no."""
    old = limits()
    new = old.model_copy(update={"sensitive_categories": {"banco": "x"}})
    assert judge(old, new).relaxed == ("sensitive_categories",)


def test_dos_campos_uno_discreto_y_otro_que_relaja_se_rechaza_entero():
    verdict = judge(limits(), limits(max_messages_per_day=6, max_deletions_per_day=8))
    assert verdict.changed == ("max_messages_per_day", "max_deletions_per_day")
    assert verdict.relaxed == ("max_deletions_per_day",)
    assert not verdict.accepted


def test_sin_cambio_de_valor_no_se_acepta():
    verdict = judge(limits(), limits())
    assert verdict.changed == () and not verdict.accepted


def test_el_presupuesto_cubre_los_cuatro_importes():
    """Si `Budget` gana un campo, este test obliga a darle su dirección."""
    assert set(Budget.model_fields) == {
        "monthly_target_usd",
        "warn_at",
        "daily_cap_usd",
        "max_session_usd",
    }


def test_la_tabla_cubre_todos_los_campos_de_limites():
    """Si `Limits` gana un campo, este test obliga a darle su dirección en `discreet.py`."""
    from jimic.memory.schemas import Limits

    assert set(Limits.model_fields) == {
        "max_messages_per_day",
        "quiet_hours",
        "silent_during_events",
        "group_window",
        "quiet_while_chatting",
        "max_insist_per_task",
        "max_deletions_per_day",
        "budget",
        "tool_levels",
        "sensitive_categories",
    }
