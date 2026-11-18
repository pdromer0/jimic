# JimiC

Agente personal proactivo que acompaña a Pedro durante el día por el móvil, conoce sus
objetivos y le ayuda a cumplirlos con datos reales: a diferencia de un chatbot, escribe primero.

- Diseño: [docs/diseno.md](docs/diseno.md)
- Hito en curso: ninguno; el H1 empieza con su sesión conceptual ([diseño, 11.1](docs/diseno.md))
- Último hito cerrado: [docs/specs/hito-0.md](docs/specs/hito-0.md) (entorno, `v0.0-hito0`)
- Ciclo de cada tarea y sus reglas: [docs/proceso/tarea.md](docs/proceso/tarea.md)
- Módulos del método activos: [docs/proceso/modulos.md](docs/proceso/modulos.md)

## Modo de trabajo

- Cada tarea sale de su issue y sigue [tarea.md](docs/proceso/tarea.md): plan, rama, commits,
  revisión propia, una N3 y PR. Las reglas de tests, de GitHub y de git viven allí.
- Explica en español el concepto antes de implementar; Pedro pregunta hasta entenderlo.
- Usa modo plan para cualquier cambio no trivial y espera la aprobación. Con el plan aprobado,
  implementa la tarea entera sin esperar revisión entre pasos.
- Para y pregunta si algo contradice el plan o la especificación, una biblioteca no se comporta
  como se esperaba, un test falla sin causa clara o hace falta una decisión de diseño nueva.
- Nunca cambies una semilla ni una tolerancia para que un test pase.
- No arranques procesos que no terminan (servidores): se prueban con dobles. Un doble que el
  código llama en bucle lleva un tope que lanza un error claro.
- `uv sync` con red (dependencias nuevas) pide permiso; `uv sync --offline --frozen`, no.
- La documentación que cambia con el código va en el mismo PR.

## Comandos

```bash
uv sync --offline --frozen   # entorno, sin red (uv.lock)
uv run pytest                # todo, incluidos los slow (como el CI)
uv run pytest -m "not slow"  # rápidos
uv run pre-commit run --all-files   # ruff, pyright, gitleaks (lo preparado) y ficheros
uv run jimic --version   # CLI
scripts/n3.sh                # antes del push: pre-commit, gitleaks de la historia y pytest
scripts/gh.sh …              # GitHub por REST (issues, PR, checks)
```

Cada test tiene un límite de tiempo (`pytest-timeout`, 60 s; los `slow`, el de `tests/conftest.py`).
Un test legítimo que lo supere se marca con `@pytest.mark.timeout(N)`, midiendo antes.

## Shell y edición

- Heredocs siempre con comillas (`<<'EOF'`); nada que espere entrada (`git --no-pager`).
- La herramienta de edición del agente antes que `sed`; nunca `sed -i` sobre markdown.
- `git push` en un comando propio, nunca encadenado.
- Los ficheros grandes generados o copiados los escribe un script, no el modelo.

## Convenciones

- Código, nombres y docstrings del paquete (`src/`) en inglés.
- Tests del núcleo: nombres, docstrings y comentarios en español; fixtures y ayudas, en inglés.
- Documentación, commits y claves de configuración en español.
- Commits con prefijo `feat`, `fix`, `refactor`, `docs`, `test`, `chore` o `ci` y ámbito.
- Decisiones difíciles de revertir, como ADR en [docs/decisiones/](docs/decisiones/README.md).

## Reglas de git

- Nunca trabajes en `main`: cada tarea en su rama y por PR. Nunca fusiones: lo hace Pedro.
- Nunca subas sin permiso. Claude Code tiene su excepción en `CLAUDE.md`.

## Review guidelines

En las revisiones de pull requests, trata como P1:

- Cambiar una semilla o una tolerancia para que un test pase sin analizar la causa.
- Lógica en la CLI o en capas finas que debería vivir en el paquete, con tests.
- Tests que pasan de forma vacía, sin comprobar que ocurre la situación que prueban.
- Código nuevo sin tests; romper la convención de idiomas de cada zona.
