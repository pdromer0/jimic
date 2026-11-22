"""Las tres operaciones de un cambio de memoria sobre el texto, como parche exacto (tarea 1.12)."""

import pytest

from jimic.memory.edits import EditError, Operation, Patch, find_once, patch_for


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a", "a\nX\n"),
        ("a\n", "a\nX\n"),
        ("", "X\n"),
        ("a\n\n", "a\n\nX\n"),
    ],
)
def test_add_anade_al_final_en_su_propia_linea(text, expected):
    """Los valores a mano del plan."""
    assert patch_for(text, "add", None, "X").apply(text) == expected


def test_add_en_un_fichero_crlf_usa_su_salto_de_linea():
    """Ajuste 10: no deja saltos mezclados, tampoco dentro de lo que se añade."""
    text = "a\r\nb"
    assert patch_for(text, "add", None, "X\nY").apply(text) == "a\r\nb\r\nX\r\nY\r\n"


def test_replace_cambia_solo_el_fragmento():
    text = "uno\ndos\ntres\n"
    patch = patch_for(text, "replace", "dos", "DOS")
    assert patch == Patch(4, "dos", "DOS")
    assert patch.apply(text) == "uno\nDOS\ntres\n"


@pytest.mark.parametrize(
    ("text", "code"),
    [("uno\ndos\n", "not_found"), ("dos dos\n", "repeated"), ("aaa", "repeated")],
)
def test_replace_y_remove_exigen_el_antes_una_sola_vez(text, code):
    """`aaa` lleva `aa` dos veces solapadas: también cuenta como repetido."""
    fragment = "aa" if text == "aaa" else "dos" if code == "repeated" else "tres"
    cases: list[tuple[Operation, str | None]] = [("replace", "x"), ("remove", None)]
    for operation, after in cases:
        with pytest.raises(EditError) as raised:
            patch_for(text, operation, fragment, after)
        assert raised.value.code == code


def test_remove_de_una_linea_entera_se_lleva_su_salto():
    text = "- uno\n- dos\n- tres\n"
    assert patch_for(text, "remove", "- dos", None).apply(text) == "- uno\n- tres\n"


def test_remove_de_un_trozo_de_linea_no_toca_el_salto():
    text = "- uno dos\n- tres\n"
    assert patch_for(text, "remove", " dos", None).apply(text) == "- uno\n- tres\n"


def test_remove_de_la_ultima_linea_sin_salto():
    text = "- uno\n- dos"
    assert patch_for(text, "remove", "- dos", None).apply(text) == "- uno\n"


def test_el_inverso_devuelve_el_texto_exacto():
    text = "\ufeffuno\r\ndos\r\n"
    cases: list[tuple[Operation, str | None, str | None]] = [
        ("add", None, "tres"),
        ("replace", "dos", "DOS"),
        ("remove", "dos", None),
    ]
    for operation, before, after in cases:
        patch = patch_for(text, operation, before, after)
        assert patch.inverse().apply(patch.apply(text)) == text


def test_aplicar_sobre_otro_texto_falla():
    with pytest.raises(EditError):
        Patch(0, "uno", "UNO").apply("dos")


def test_find_once_da_la_posicion():
    assert find_once("abc", "b") == 1
