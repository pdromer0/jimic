# shellcheck shell=bash
# Parte de la N3 (añadido de JimiC, decisión 49): gitleaks sobre toda la historia. El hook de
# cada commit solo mira lo preparado y, en la N3, no revisaría nada.
parte gitleaks-historia uv run pre-commit run --hook-stage manual gitleaks-historia --verbose
