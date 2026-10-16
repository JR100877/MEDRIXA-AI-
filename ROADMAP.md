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

Actualización P4: el punto 4 se dividió. **P4 (persistencia y recuperación) está implementado** en `dev/p4-persistence`, con 107 pruebas y sin fusionar hasta la autorización. **P4b** queda pendiente: adaptador FastAPI local y orden del adaptador PostgreSQL. P5 y P6 no cambian.

Actualización P4b (autorización de Robinson del 15/10, 21:39): **P4b está implementado** en `dev/p4b-http`, con 154 pruebas y sin fusionar. Robinson propuso una nueva planificación, que reemplaza la numeración P5/P6 anterior. **Los identificadores son una propuesta, no fases aprobadas:**
- **P5:** adaptador PostgreSQL compatible con `StateRepository`.
- **P6:** arrendamiento de ejecuciones, coordinación y recuperación entre procesos.
- **P7:** endurecimiento de seguridad (secretos, cifrado en reposo, controles de acceso, OIDC).
- **P8:** observabilidad, métricas, auditoría y diagnóstico.
- **P9:** integración, compatibilidad y preparación para despliegues controlados (sin despliegue productivo).

El adaptador FHIR y el Knowledge/AI Engine (antes P5/P6) siguen pendientes. Requieren decisiones clínicas y regulatorias; se reprogramarán con Robinson.
