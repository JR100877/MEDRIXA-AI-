# Registro de cambios

## P1 — 2026-10-15 (Colombia)

Código nuevo: `app/models/contracts.py`, `app/core/state.py`, `app/core/state_manager.py`, pruebas en `tests/core/test_state.py` y utilidades. Documentos: arquitectura, decisiones, roadmap, test status y continuidad. Resultado: 17 pruebas aprobadas. Pendiente: flujo de agentes.

## P0 — 2026-10-15 (Colombia)

Base aislada y persistida; antecedentes documentados sin modificación. No había fuente histórica ni versión ejecutable confirmada.

## P2 — 2026-10-15 (Colombia)

Planner puro, Router con registro explícito y Orchestrator asíncrono acotado; HistoryAgent y ClinicalAgent simulados. Nuevas pruebas: 22 aprobadas, con fallos, cancelación, timeout y ocho casos concurrentes. Evidencia: `evidence/P2_tests.txt`. Riesgo: timeout cooperativo no detiene código no confiable; solo agentes locales de confianza. Siguiente: P3, contratos exportables, CLI y endurecimiento.

## P3 — 2026-10-15 (Colombia)

CLI reproducible, frontera JSON local de 1 MiB, rechazo de duplicados/campos extra, marcadores sintéticos explícitos, validación estructural del historial, orden de agentes independiente del Planner y rechazo de respuestas posteriores al timeout. Resumen de observabilidad sin payload. Ocho esquemas JSON nuevos, fixtures y documentación de API futura. 13 pruebas nuevas; regresión final de 52 aprobadas.

Componentes afectados: contracts.py, state.py, orchestrator.py, helpers/pruebas de P1; nuevos app/api/contracts.py, app/bootstrap.py, app/__main__.py, app/observability/summary.py, scripts/export_schemas.py y tests/test_boundary.py. Fuentes completas en el manifest del paquete. Riesgos: estado en memoria, timeout cooperativo y ausencia de validación clínica. Siguiente: P4, puerto de repositorio y recuperación transaccional segura.
