import pytest

SLOW_TIMEOUT = 600
"""Segundos por test `slow`: un tope contra un bucle sin fin, no una medida."""


def pytest_collection_modifyitems(items):
    """Da a los tests `slow` su propio límite de tiempo, mayor que el de los demás."""
    for item in items:
        if item.get_closest_marker("slow") and not item.get_closest_marker("timeout"):
            item.add_marker(pytest.mark.timeout(SLOW_TIMEOUT))
