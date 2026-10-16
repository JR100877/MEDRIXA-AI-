# Hoja de ruta incremental

1. P1: contratos, State, StateManager — implementado, 17 pruebas aprobadas.
2. P2: Planner, Router, Orchestrator y dos agentes sintéticos; pruebas de integración.
3. P3: fallos, límites, esquemas, simulador ejecutable y evidencia de continuidad.
4. P4: persistencia transaccional de ejecuciones y adaptador API FastAPI, manteniendo contratos y revisando recuperación tras caída.
5. P5: adaptador FHIR con versión y perfiles definidos, validación de conformidad sintética; no conectar hospital.
6. P6: Knowledge/AI Engine con fuentes autorizadas, evaluación y revisión experta antes de reglas clínicas o recomendaciones.

Recuperar el código histórico es una tarea paralela opcional: si aparece, conservarlo separado y comparar contratos antes de integrar. No bloquear los pasos independientes.

Actualización P2: paquete 2 implementado; próximo P3.

Actualización P3: paquetes 1–3 implementados y verificados. Próximo paso único: P4, puerto StateRepository y persistencia/recuperación transaccional sintética, antes del adaptador FastAPI. Ver `docs/NEXT_PACKAGE.md`.
