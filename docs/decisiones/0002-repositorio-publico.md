# 0002. Repositorio público sin nada personal

- **Estado:** Aceptada
- **Fecha:** 2026-11-17

## Contexto

JimiC toca datos personales de Pedro (agenda, tareas, entrenamiento y, más adelante, salud y
correo). El núcleo del método no dice si el repositorio es público o privado. Con una cuenta
personal de GitHub, un repositorio privado no tiene ruleset efectivo sin GitHub Pro, ni escaneo de
secretos ni CodeQL. Desviación del núcleo: **añade** piezas, no cambia ninguna decisión de fondo
(ADR [0001](0001-adoptamos-el-metodo-edsim.md)).

## Decisión

El repositorio `pdromer0/jimic` es **público y sin nada personal** (decisión 43). La
configuración, los secretos, los ficheros personales, la base de datos, los casos reales de
evaluación y lo bruto del piloto viven fuera del repositorio; los tests usan datos sintéticos; nada
personal en issues, PR, «Notas del chat», commits ni logs (regla en `docs/proceso/tarea.md` y
aviso en las plantillas). Con sus protecciones:

- Escaneo de secretos y bloqueo al hacer push de GitHub.
- CodeQL con la configuración por defecto y, en el ruleset, «Require code scanning results» con
  las alertas «High or higher» (decisión 50).
- El CI de los PR desde *forks* exige la aprobación de Pedro para todos los contribuidores
  externos; informe privado de vulnerabilidades con un `SECURITY.md` corto (decisión 52).
- Sin licencia por ahora (decisión 53): el código se puede leer, pero no reutilizar.
- Las acciones del CI, fijadas por el SHA del commit, con la versión en un comentario: en un
  repositorio público, una etiqueta movida en una acción de terceros ejecutaría código ajeno.

## Alternativas consideradas

- Privado con GitHub Pro (4 $/mes): sin escaneo de secretos ni CodeQL en un repositorio privado
  personal. Tendría sentido si entrara algo sensible que no se pudiera generalizar.
- Privado sin Pro: `main` sin protección real. No tiene sentido con el método, que se apoya en el
  ruleset.
- Una organización con Team: cambia la cuenta (P2) y cuesta dinero. Tendría sentido con más
  personas en el proyecto.

## Consecuencias

- Ruleset, escaneo de secretos, bloqueo al hacer push y CodeQL gratis, y un portafolio público.
- El precio es una disciplina permanente: un descuido en un issue o un PR públicos no se deshace.
  Lo personal se generaliza («un día de ejemplo», «una prioridad diaria»).
- CodeQL añade unos minutos a cada PR.

## Qué cambió al implementarla

- Fase 3 (PR #1, 4948329): las tres acciones del CI, fijadas por SHA (checkout v7.0.1, setup-uv
  v10.3.3 y cache v6.1.0). La aprobación de los PR de fuera estaba en `first_time_contributors`
  (el valor por defecto) y se cambió a `all_external_contributors` por la API, con el permiso de
  Pedro (`arranque.md`, incidencias de la fase 3).
- Fase 4 (PR #2, 04066d4): `SECURITY.md` y las reglas «nada personal» y «lo bruto del piloto» en
  `tarea.md`.
- Fase 5 (PR #3, fc29a79): el aviso «Nada personal» en las plantillas y `config.yml` sin issues en
  blanco y con el enlace al informe privado.
