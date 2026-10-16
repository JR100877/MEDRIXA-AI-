# P4b — siguiente paquete exacto

P4 (puerto `StateRepository`, adaptadores provisionales y recuperación) está implementado en la rama `dev/p4-persistence`, pendiente de autorización para fusionarse. Ver `TEST_STATUS.md` y `docs/PERSISTENCE.md`.

## Objetivo

Exponer el servicio persistente mediante un adaptador HTTP FastAPI local, sin despliegue, conservando los contratos `medrixa.prototype.v1` y la revisión humana terminal.

## Orden de ejecución

1. Abrir `CONTINUITY.json` y `TEST_STATUS.md` y verificar el manifest. No repetir las 107 pruebas si el código no cambió.
2. Confirmar si FastAPI y un cliente de pruebas pueden instalarse. Si no es posible, dejar HTTP pendiente sin fingir un servidor.
3. Crear una capa de servicio (crear ejecución, consultar resumen y recuperar) sobre `StateRepository`. HTTP solo traduce: reutiliza `parse_case` y los errores uniformes, y no devuelve observaciones.
4. Antes de exponer cualquier ruta, definir con el responsable: autenticación, autorización por caso, límites de petición, idempotencia de creación (clave de idempotencia), códigos HTTP para `RevisionConflict`/`DuplicateRun` y exclusión de cuerpos en los logs.
5. Decidir si el adaptador PostgreSQL entra antes o después de HTTP. Debe pasar `tests/persistence/test_repository_contract.py` sin cambios. Un PostgreSQL local o en contenedor no implica costos; un servicio gestionado requiere aprobación.
6. Opcional: arrendamiento/latido de ejecuciones, para que la recuperación pueda correr con ejecutores vivos.

## Criterio de aceptación

Pruebas HTTP locales para entrada inválida, conflicto, duplicado y recuperación; sin servidor público, sin credenciales reales y sin datos reales.

## Condiciones de aprobación

Fusionar `dev/p4-persistence` requiere autorización de Robinson. También requieren aprobación: producción, datos reales, costos, acceso hospitalario o un cambio incompatible de contratos o de la arquitectura histórica.
