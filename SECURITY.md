# Seguridad

## Cómo avisar

Si encuentras una vulnerabilidad, avisa por el **informe privado de vulnerabilidades** de GitHub:
pestaña *Security* → *Report a vulnerability*
([enlace directo](https://github.com/pdromer0/jimic/security/advisories/new)). No abras un issue
público ni pongas datos reales en el aviso: si hace falta un ejemplo, invéntalo.

## Qué interesa

- **Saltarse la guardia**: que JimiC haga algo que sus límites o la aprobación de Pedro deberían
  impedir.
- **Fugas de datos personales**: que algo personal llegue al repositorio, a los logs, a un issue o
  a un servicio que no debería verlo.
- **Inyección de instrucciones**: un correo, un evento o cualquier texto de fuera que consiga que
  el agente obedezca lo que dice.
