"""El mensaje interactivo, sin Telegram ni SQLite: filas, textos, topes y estado."""

import json

import pytest
from pydantic import JsonValue, ValidationError

from jimic.channel.message import (
    MAX_APPROVAL_ID,
    MAX_ITEMS,
    GlobalOption,
    InteractiveMessage,
    Item,
    MessageTooLong,
    Option,
    Outcome,
    Tap,
    claim,
    clip_result,
    ensure_fits,
    expire,
    final_status,
    fit_titles,
    parse_callback,
    release,
    render,
    resolve,
    targets,
)

YES = Option(key="y", label="Sí")
NO = Option(key="n", label="No", approves=False)
TITLES = ["Gimnasio 13:00", "Llamar a la tienda 17:30", "Leer 20 minutos 21:00"]


def make_message(
    titles: list[str] = TITLES, header: str = "Plan de prueba", options=(YES, NO)
) -> InteractiveMessage:
    return InteractiveMessage(
        header=header,
        items=tuple(Item(title=title, options=options) for title in titles),
        global_options=(
            GlobalOption(key="all", label="Créalo todo", option="y"),
            GlobalOption(key="none", label="Nada", option="n"),
        ),
    )


def labels(view) -> list[list[str]]:
    return [[button.label for button in row] for row in view.keyboard]


def test_un_mensaje_con_n_elementos_lleva_n_filas_numeradas_y_la_global():
    """Pilla un teclado sin fila por elemento, sin numerar o sin la fila global."""
    view = render(make_message(), approval_id=7)
    assert labels(view) == [
        ["1 Sí", "1 No"],
        ["2 Sí", "2 No"],
        ["3 Sí", "3 No"],
        ["Créalo todo", "Nada"],
    ]
    assert view.text == (
        "<b>Plan de prueba</b>\n\n1. Gimnasio 13:00\n2. Llamar a la tienda 17:30\n"
        "3. Leer 20 minutos 21:00"
    )


@pytest.mark.parametrize("count", [1, MAX_ITEMS])
def test_el_minimo_y_el_maximo_de_elementos(count):
    """Los bordes: un elemento (2 filas) y ocho (9 filas)."""
    view = render(make_message([f"tarea {n}" for n in range(count)]), approval_id=1)
    assert len(view.keyboard) == count + 1


@pytest.mark.parametrize("count", [0, MAX_ITEMS + 1])
def test_cero_o_nueve_elementos_son_un_error(count):
    """Pilla un tope de elementos que no se aplica por arriba ni por abajo."""
    with pytest.raises(ValidationError):
        make_message([f"tarea {n}" for n in range(count)])


def test_un_toque_quita_su_fila_y_el_texto_dice_que_paso():
    """Quedan las filas de lo pendiente y la global; el elemento lleva su resultado (1.4-b)."""
    message = make_message()
    tap = Tap(approval_id=7, item=1, option="y")
    assert targets(message, tap) == [(0, "y")]
    after = resolve(message, {0: Outcome("y", "✓ creada")})
    view = render(after, approval_id=7)
    assert labels(view) == [["2 Sí", "2 No"], ["3 Sí", "3 No"], ["Créalo todo", "Nada"]]
    assert "1. Gimnasio 13:00 — ✓ creada" in view.text
    assert "2. Llamar a la tienda 17:30\n" in view.text


@pytest.mark.parametrize(
    ("count", "rows"), [(3, [4, 3, 2, 0]), (1, [2, 0]), (8, [9, 8, 7, 6, 5, 4, 3, 2, 0])]
)
def test_las_filas_van_bajando_y_al_resolver_todo_no_queda_teclado(count, rows):
    """Valores a mano: con N pendientes hay N filas y la global; sin pendientes, ninguna."""
    message = make_message([f"tarea {n}" for n in range(count)])
    seen = [len(render(message, 1).keyboard)]
    for index in range(count):
        message = resolve(message, {index: Outcome("y", "ok")})
        seen.append(len(render(message, 1).keyboard))
    assert seen == rows


def test_lo_ya_resuelto_no_se_vuelve_a_tocar():
    """Un segundo toque sobre lo resuelto no toca ningún elemento ni cambia el mensaje."""
    message = resolve(make_message(), {0: Outcome("y", "✓ creada")})
    assert targets(message, Tap(7, 1, "n")) == []
    assert resolve(message, {0: Outcome("n", "✗ descartada")}) == message


def test_creelo_todo_resuelve_lo_pendiente_y_no_toca_lo_resuelto():
    """La fila global actúa sobre lo pendiente que ofrece su opción, y solo sobre eso."""
    message = resolve(make_message(), {1: Outcome("n", "✗ descartada")})
    assert targets(message, Tap(7, None, "all")) == [(0, "y"), (2, "y")]
    assert targets(message, Tap(7, None, "none")) == [(0, "n"), (2, "n")]


def test_la_global_no_resuelve_lo_que_no_ofrece_su_opcion():
    """Un borrado (sin la opción `y`) sigue pendiente tras [Créalo todo], y su fila con él."""
    delete = Item(title="Borrar la tarea vieja", options=(Option(key="del", label="Borrar"),))
    message = InteractiveMessage(
        header="Mezcla",
        items=(Item(title="Crear una", options=(YES, NO)), delete),
        global_options=(GlobalOption(key="all", label="Créalo todo", option="y"),),
    )
    assert targets(message, Tap(1, None, "all")) == [(0, "y")]
    after = resolve(message, {0: Outcome("y", "✓ creada")})
    assert labels(render(after, 1)) == [["2 Borrar"]]


def test_un_toque_que_no_existe_no_toca_nada():
    """Elemento fuera de rango, opción que no ofrece o global desconocida: ningún objetivo."""
    message = make_message()
    assert targets(message, Tap(1, 4, "y")) == []
    assert targets(message, Tap(1, 1, "zzz")) == []
    assert targets(message, Tap(1, None, "zzz")) == []


def test_callback_data_cabe_en_64_bytes_y_no_lleva_el_titulo():
    """Pilla un título en `callback_data`: el título largo lo haría pasar de 64 bytes."""
    title = "Título larguísimo y secreto " * 20
    message = InteractiveMessage(
        header="H",
        items=(Item(title=title, options=(Option(key="a" * 8, label="x"),)),),
        global_options=(GlobalOption(key="b" * 8, label="g", option="a" * 8),),
    )
    view = render(message, approval_id=MAX_APPROVAL_ID)
    data = [button.data for row in view.keyboard for button in row]
    assert data == [f"{MAX_APPROVAL_ID}:1:aaaaaaaa", f"{MAX_APPROVAL_ID}:g:bbbbbbbb"]
    assert [len(item.encode()) for item in data] == [30, 30]
    assert all("Título" not in item and "secreto" not in item for item in data)


def test_el_callback_data_del_teclado_se_entiende_como_toque():
    """Ida y vuelta: lo que sale en cada botón es un toque que se parsea al mismo botón."""
    view = render(make_message(), approval_id=42)
    taps = [parse_callback(button.data) for row in view.keyboard for button in row]
    assert taps[:2] == [Tap(42, 1, "y"), Tap(42, 1, "n")]
    assert taps[-2:] == [Tap(42, None, "all"), Tap(42, None, "none")]
    assert None not in taps


@pytest.mark.parametrize(
    "data",
    [
        "",
        "x",
        "1:2",
        "1:2:y:z",
        "a:1:y",
        "0:1:y",
        "01:1:y",
        "+1:1:y",
        "-1:1:y",
        "1:0:y",
        "1:9:y",
        "1:1:Y",
        "1:1:muylargo99",
        "1:1:y\n",
        "1: 1:y",
        f"{MAX_APPROVAL_ID + 1}:1:y",
        "١:1:y",  # un dígito árabe: no es de los nuestros
    ],
)
def test_un_callback_data_mal_formado_no_es_un_toque(data):
    """Pilla un parseo que acepta signos, ceros, otros alfabetos o un id fuera de rango."""
    assert parse_callback(data) is None


def test_el_titulo_y_los_resultados_salen_escapados():
    """Pilla un título con `<b>`, `&` o `*` que llega sin escapar (o que se toma por Markdown)."""
    message = make_message(["<b>negrita</b> & a*b_c"], header="<i>x</i> & y")
    text = render(message, 1).text
    assert (
        text == "<b>&lt;i&gt;x&lt;/i&gt; &amp; y</b>\n\n1. &lt;b&gt;negrita&lt;/b&gt; &amp; a*b_c"
    )
    after = resolve(message, {0: Outcome("y", "<ok> & listo")})
    assert render(after, 1).text.endswith("a*b_c — &lt;ok&gt; &amp; listo")


@pytest.mark.parametrize(
    ("title", "fits"),
    [
        ("x" * 3880, True),  # 8 + 2 + 3 + 3880 + 3 + 200 (40 `&` escapados) = 4096
        ("x" * 3881, False),
        ("&" * 776, True),  # escapado: 776 × 5 = 3880, y el límite es sobre el HTML escapado
        ("&" * 777, False),
        ("🏋" * 1940, True),  # 2 unidades UTF-16 cada uno: 3880
        ("🏋" * 1941, False),
    ],
)
def test_el_tope_de_4096_mide_el_html_escapado_en_utf16_con_el_peor_resultado(title, fits):
    """Valores a mano. Pilla un tope que mide el texto sin escapar, en caracteres o sin dejar
    sitio al resultado que un toque añadirá después."""
    message = make_message([title], header="H")
    if fits:
        ensure_fits(message)
    else:
        with pytest.raises(MessageTooLong, match="4096"):
            ensure_fits(message)


def test_un_mensaje_con_ocho_elementos_normales_cabe():
    ensure_fits(make_message([f"tarea número {n}" for n in range(MAX_ITEMS)]))


def test_el_estado_va_y_vuelve_por_json():
    """Lo que se guarda en `approvals.payload` reconstruye el mismo mensaje, ya resuelto."""
    message = resolve(make_message(), {0: Outcome("y", "✓ creada")})
    payload = message.model_dump_json()
    assert json.loads(payload)["items"][0]["result"] == "✓ creada"
    assert InteractiveMessage.model_validate_json(payload) == message


@pytest.mark.parametrize(
    "bad",
    [
        dict(title="  ", options=(YES,)),
        dict(title="t", options=()),
        dict(title="t", options=(YES, YES)),
        dict(title="t", options=tuple(Option(key=f"k{n}", label="x") for n in range(5))),
        dict(title="t", options=(YES,), chosen="y"),
        dict(title="t", options=(YES,), chosen="n", result="ok"),
    ],
)
def test_un_elemento_incoherente_es_un_error(bad):
    """Título vacío, sin opciones, claves repetidas, 5 opciones o una elegida que no existe."""
    with pytest.raises(ValidationError):
        Item(**bad)


def test_caducar_marca_lo_pendiente_quita_el_teclado_y_respeta_lo_resuelto():
    """Un toque caducado marca «caducado» y quita el teclado; lo ya resuelto se queda."""
    message = resolve(make_message(), {0: Outcome("y", "✓ creada")})
    after = expire(message)
    view = render(after, 7)
    assert view.keyboard == ()
    assert "1. Gimnasio 13:00 — ✓ creada" in view.text
    assert "2. Llamar a la tienda 17:30 — caducado" in view.text
    assert targets(after, Tap(7, 2, "y")) == []


def test_el_estado_final_de_la_aprobacion():
    """Pendiente: nada; algo aprobado: `approved`; solo noes: `rejected`; caducado: `expired`."""
    message = make_message(["a", "b"])
    assert final_status(message) is None
    assert final_status(resolve(message, {0: Outcome("y", "ok")})) is None
    assert final_status(resolve(message, {0: Outcome("y", "ok"), 1: Outcome("n", "no")})) == (
        "approved"
    )
    assert final_status(resolve(message, {0: Outcome("n", "no"), 1: Outcome("n", "no")})) == (
        "rejected"
    )
    assert final_status(expire(message)) == "expired"
    assert final_status(
        expire(resolve(message, {0: Outcome("y", "ok"), 1: Outcome("y", "ok")}))
    ) == ("expired")


def test_el_resultado_largo_se_recorta_y_queda_en_una_linea():
    """Un manejador que devuelve mucho o con saltos de línea no rompe el tope del mensaje."""
    assert clip_result("ok") == "ok"
    assert clip_result("a" * 40) == "a" * 40
    assert clip_result("a" * 41) == "a" * 39 + "…"
    assert clip_result("uno\ndos\n\ntres") == "uno dos tres"
    assert clip_result("  \n ") == "hecho"


# --- Lo que añade la 1.11: cuerpo, nota, «en curso» y títulos que caben --------------------


def test_el_cuerpo_va_antes_de_la_cabecera_y_se_escapa():
    """Pilla el texto del agente sin escapar, en negrita o detrás de la lista."""
    message = make_message(TITLES[:1]).model_copy(update={"body": "Mira <esto> & aquello"})
    assert render(message, 1).text == (
        "Mira &lt;esto&gt; &amp; aquello\n\n<b>Plan de prueba</b>\n\n1. Gimnasio 13:00"
    )


def test_sin_cabecera_ni_cuerpo_no_hay_mensaje():
    with pytest.raises(ValidationError):
        InteractiveMessage(items=(Item(title="x", options=(YES,)),))


def test_la_nota_se_ve_y_el_elemento_conserva_su_fila():
    """Pilla un fallo que quita la fila (Pedro no podría reintentarlo) o que no se ve."""
    message = make_message(TITLES[:1])
    item = message.items[0].model_copy(update={"note": "falló (timeout)"})
    view = render(message.model_copy(update={"items": (item,)}), 1)
    assert view.text.endswith("1. Gimnasio 13:00 — falló (timeout)")
    assert labels(view)[0] == ["1 Sí", "1 No"]


def test_un_elemento_en_curso_no_tiene_fila_ni_lo_toca_un_toque():
    """Pilla un elemento reclamado que aún se puede tocar: su efecto correría dos veces."""
    message = claim(make_message(), {0})
    view = render(message, 1)
    assert "1. Gimnasio 13:00 — en curso…" in view.text
    assert labels(view)[0] == ["2 Sí", "2 No"]
    assert targets(message, Tap(1, 1, "y")) == []
    assert targets(message, Tap(1, None, "all")) == [(1, "y"), (2, "y")]
    assert final_status(resolve(message, {1: Outcome("y", "ok"), 2: Outcome("y", "ok")})) is None


def test_resolver_un_elemento_en_curso_lo_cierra_y_borra_su_nota():
    item = make_message().items[0].model_copy(update={"running": True, "note": "falló (x)"})
    message = make_message().model_copy(update={"items": (item,)})
    done = resolve(message, {0: Outcome("y", "✓ creada")}).items[0]
    assert (done.result, done.running, done.note) == ("✓ creada", False, None)


def test_al_caducar_lo_pendiente_dice_su_texto_y_lo_que_estaba_en_curso_queda_sin_confirmar():
    """Pilla un efecto a medias que se da por caducado (podría haber ocurrido)."""
    message = claim(make_message(), {0}).model_copy(update={"expired_text": "Deshacer caducado"})
    expired = expire(message)
    assert [item.result for item in expired.items] == [
        "sin confirmar: mira Todoist",
        "Deshacer caducado",
        "Deshacer caducado",
    ]
    assert not any(item.running for item in expired.items)


def test_una_nota_de_mas_de_40_no_entra():
    with pytest.raises(ValidationError):
        Item(title="x", options=(YES,), note="n" * 41)


def test_fit_titles_recorta_solo_lo_que_se_ve_hasta_que_cabe():
    """Pilla un mensaje que no cabe tras renovar un título largo, o que pierde lo que hace."""
    data: dict[str, JsonValue] = {"task_id": "t1"}
    items = tuple(Item(title="x" * 500, options=(YES, NO), data=data) for _ in range(MAX_ITEMS))
    message = InteractiveMessage(header="Plan", items=items)
    with pytest.raises(MessageTooLong):
        ensure_fits(message)
    fitted = fit_titles(message)
    ensure_fits(fitted)
    assert all(item.title.endswith("…") and item.data == data for item in fitted.items)
    assert fit_titles(make_message()) == make_message()


def test_release_vuelve_a_dejar_pendiente_y_reajusta_el_titulo():
    message = claim(make_message(), {0})
    renewed = message.items[0].model_copy(update={"title": "y" * 5000, "note": "ha cambiado"})
    released = release(message, 0, renewed)
    item = released.items[0]
    assert item.pending and item.note == "ha cambiado" and item.title.endswith("…")
    ensure_fits(released)


def test_release_nunca_lanza_si_el_titulo_nuevo_no_cabe_se_queda_el_anterior():
    """Hallazgo 5 de la revisión: `release` corre tras los efectos de un toque; si lanzara, lo
    ya hecho quedaría «en curso». Un cuerpo al límite y ocho títulos cortos no dejan sitio a un
    título renovado, que no se recorta por debajo de 20: se queda el que había, con su nota y lo
    nuevo en `data`."""
    items = tuple(Item(title="t" * 5, options=(YES, NO)) for _ in range(MAX_ITEMS))
    body = "b" * 1
    message = InteractiveMessage(header="Plan", body=body, items=items)
    for step in (50, 1):  # el cuerpo más largo que aún cabe, al carácter
        while True:
            try:
                ensure_fits(message.model_copy(update={"body": body + "b" * step}))
            except MessageTooLong:
                break
            body += "b" * step
    message = claim(message.model_copy(update={"body": body}), {0})
    renewed = message.items[0].model_copy(
        update={"title": "n" * 200, "note": "ha cambiado", "data": {"x": 1}}
    )
    released = release(message, 0, renewed)
    item = released.items[0]
    assert (item.title, item.note, item.data, item.pending) == (
        "t" * 5,
        "ha cambiado",
        {"x": 1},
        True,
    )
    ensure_fits(released)


# --- El plan del día (tarea 1.14) -------------------------------------------------------------

KEEP = Option(key="k", label="Déjala", approves=False)
DELETE = Option(key="d", label="Bórrala")


def test_el_estilo_del_plan_numera_con_teclas_y_pone_el_pie_tras_los_elementos():
    """Pilla el estilo del plan con `1.`, con la cabecera en negrita y una línea en blanco (no es
    el formato de la especificación) o sin el pie."""
    message = InteractiveMessage(
        body="¡Pedro! Hola & buenos días",
        header="🧩 Te propongo añadir",
        items=(Item(title="🏋️ Gimnasio <pierna>", options=(YES, NO)),),
        footer="🏋️ Hoy toca pierna",
        style="plan",
    )
    assert render(message, 1).text.split("\n") == [
        "¡Pedro! Hola &amp; buenos días",
        "",
        "🧩 Te propongo añadir",
        "1️⃣ 🏋️ Gimnasio &lt;pierna&gt;",
        "",
        "🏋️ Hoy toca pierna",
    ]


def test_la_global_con_then_aplica_la_otra_opcion_a_quien_no_ofrece_la_primera():
    """Pilla un `then` que no se aplica (el borrado se quedaría con su fila) o que se aplica a
    quien sí ofrece la primera."""
    message = InteractiveMessage(
        header="Plan",
        items=(
            Item(title="Crear", options=(YES, NO)),
            Item(title="Borrar", options=(DELETE, KEEP)),
        ),
        global_options=(
            GlobalOption(key="all", label="Todo", option="y"),
            GlobalOption(key="none", label="Nada", option="n", then="k"),
        ),
    )
    assert targets(message, Tap(1, None, "none")) == [(0, "n"), (1, "k")]
    assert targets(message, Tap(1, None, "all")) == [(0, "y")]
    only_delete = message.model_copy(update={"items": message.items[1:]})
    assert labels(render(only_delete, 1))[-1] == ["Nada"]


def test_un_payload_guardado_antes_de_then_sigue_cargando():
    """Las aprobaciones de hoy guardadas antes de esta versión no llevan `then`."""
    saved = make_message().model_dump(mode="json", exclude={"global_options": {0: {"then"}}})
    for option in saved["global_options"]:
        option.pop("then", None)
    loaded = InteractiveMessage.model_validate(saved)
    assert loaded.global_options[1].then is None


# --- El estilo de las tareas clave (tarea 1.15) -------------------------------------------------

KEY_OPTIONS = (
    Option(key="go", label="🏃 Voy ahora", approves=False),
    Option(key="later", label="⏰ Más tarde", approves=False),
    Option(key="done", label="✅ Ya entrené"),
    Option(key="skip", label="⏭ Hoy no", approves=False),
)


def key_message(*icons: str | None) -> InteractiveMessage:
    items = tuple(
        Item(title=f"Tarea {number}", options=KEY_OPTIONS, icon=icon)
        for number, icon in enumerate(icons, start=1)
    )
    return InteractiveMessage(body="Oye, que la pesa te echa de menos 😄", items=items, style="key")


def test_con_una_tarea_clave_dos_filas_de_dos_sin_numero_ni_icono():
    """1.15-c: [🏃 Voy ahora] [⏰ Más tarde] / [✅ Ya entrené] [⏭ Hoy no]."""
    view = render(key_message("🏋️"), 7)
    assert labels(view) == [["🏃 Voy ahora", "⏰ Más tarde"], ["✅ Ya entrené", "⏭ Hoy no"]]
    assert [button.data for button in view.keyboard[1]] == ["7:1:done", "7:1:skip"]


def test_con_dos_tareas_clave_cada_boton_lleva_el_icono_de_la_suya():
    """Versión incorrecta 4 en el canal: cada botón apunta a su elemento, con su icono."""
    view = render(key_message("🏋️", None), 7)
    assert labels(view)[0] == ["🏋️ 🏃 Voy ahora", "🏋️ ⏰ Más tarde"]
    assert labels(view)[2] == ["2️⃣ 🏃 Voy ahora", "2️⃣ ⏰ Más tarde"]  # sin icono, su número
    assert {button.data.split(":")[1] for button in view.keyboard[3]} == {"2"}


def test_el_texto_es_solo_el_cuerpo_hasta_que_un_toque_lo_resuelve():
    message = key_message("🏋️", "🛒")
    assert render(message, 7).text == "Oye, que la pesa te echa de menos 😄"
    tapped = resolve(message, {0: Outcome("go", "🏃 a por ello")})
    view = render(tapped, 7)
    assert view.text == "Oye, que la pesa te echa de menos 😄\n\nTarea 1 — 🏃 a por ello"
    assert len(view.keyboard) == 2  # solo quedan las filas de la otra


def test_un_mensaje_de_tareas_clave_cabe_en_todos_sus_estados():
    ensure_fits(key_message("🏋️", "🛒", "📚"))
