# P4 — siguiente paquete exacto

## Objetivo

Dar persistencia transaccional a ejecuciones sintéticas, conservando el State validado, la revisión optimista y el historial de eventos. Es el siguiente riesgo concreto: hoy una caída pierde el estado de la ejecución.

## Orden de ejecución

1. Abrir `CONTINUITY.json` y `TEST_STATUS.md`; verificar el manifest y el commit del paquete. No repetir búsquedas históricas ni las 52 pruebas si no hay cambios que lo justifiquen.
2. Definir `StateRepository` como puerto (crear, cargar, guardar con revisión esperada), con contrato de conflicto explícito. Mantener PostgreSQL como destino histórico; un adaptador local de pruebas debe etiquetarse como tal y no cambiar esa decisión.
3. Implementar pruebas de contrato de repositorio: dos escritores con una revisión, snapshot/eventos atómicos, corrupción, duplicación de run y aislamiento entre casos.
4. Diseñar recuperación segura de ejecución interrumpida: pendiente sin resultado pasa a revisión; no repetir agentes automáticamente ni suponer idempotencia.
5. Solo después preparar el adaptador FastAPI sobre ese servicio. Si la dependencia no puede obtenerse, continuar puerto/repositorio/contratos con recursos locales, dejando HTTP pendiente sin fingir servidor operativo.

## Criterio de aceptación

Una ejecución sintética completa y otra interrumpida conservan estado y eventos al reiniciar el proceso; ningún resultado se acepta dos veces ni se cruza entre casos. Pruebas focalizadas de repositorio y de los puntos del CORE modificados. Sin conexión hospitalaria, publicación ni datos reales.

## Condiciones de aprobación

No se requiere aprobación para implementación local aislada. Sí para producción, datos reales, costos, acceso hospitalario o cambio incompatible de la arquitectura histórica. Si se recupera código original, comparar sin sobrescribir ni declarar compatibilidad antes de demostrarla.
