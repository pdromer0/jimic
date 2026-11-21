"""El mensaje interactivo, sin Telegram ni SQLite: filas, textos, topes y estado."""

import json

import pytest
from pydantic import ValidationError

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
    clip_result,
    ensure_fits,
    expire,
    final_status,
    parse_callback,
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
        ["1 · Sí", "1 · No"],
        ["2 · Sí", "2 · No"],
        ["3 · Sí", "3 · No"],
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
    assert labels(view) == [["2 · Sí", "2 · No"], ["3 · Sí", "3 · No"], ["Créalo todo", "Nada"]]
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
    assert labels(render(after, 1)) == [["2 · Borrar"]]


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
