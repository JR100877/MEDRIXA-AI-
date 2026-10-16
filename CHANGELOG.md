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

## P4 — 2026-10-15 (Colombia), rama `dev/p4-persistence`

Puerto `StateRepository` con compare-and-swap de una transición, historial solo extensible y errores explícitos (`DuplicateRun`, `RunNotFound`, `CorruptRecord`, `RevisionConflict`, `IntegrityViolation`). Adaptadores **provisionales** en memoria y en archivo local (escritura atómica, fsync, bloqueo por ejecución, SHA-256). `PersistentStateManager` persiste antes de publicar. Recuperación fail-closed: lo interrumpido pasa a revisión `INTERRUPTED` sin reejecutar agentes. CLI `--store` y `--recover` con salida solo de contadores. Cambio aditivo de contrato: `StopCode` + `INTERRUPTED`; nuevos esquemas `RecoveryReport` y `RecoverySummary`.

Componentes: nuevo `app/persistence/` (6 módulos) y `tests/persistence/` (7 archivos); modificados `state_manager.py` (gancho `_publish`), `contracts.py`, `observability/summary.py`, `__main__.py`, `scripts/export_schemas.py` y 3 esquemas. Pruebas previas sin modificar. 55 pruebas nuevas; regresión 107/107. PostgreSQL y FastAPI no implementados. Siguiente: P4b (`docs/NEXT_PACKAGE.md`).

## P4b — 2026-10-15 (Colombia), rama `dev/p4b-http` (base `dev/p4-persistence` @ 97592da)

Adaptador HTTP FastAPI local `medrixa.http.v1`: 5 endpoints, contrato OpenAPI publicado (`contracts/http/openapi.v1.json`), servicio independiente del transporte (`RunService`), autenticación por puerto con token HMAC **provisional**, autorización por roles y caso con denegación por defecto y 404 en accesos cruzados, idempotencia por `run_id` determinista (duradera entre reinicios), catálogo de errores uniforme con `request_id`, límites de entrada, cabeceras de seguridad, auditoría de accesos que falla cerrada, CLI `python -m app.web` (tokens locales y servidor solo en 127.0.0.1). Cambios aditivos en código previo: `parse_strict` (refactor sin cambio de comportamiento de `parse_case`) y `run_id` opcional en `PersistentStateManager`. Dependencias fijadas en `requirements*.txt`. 47 pruebas nuevas; regresión 154/154. Defecto hallado y corregido con servidor real: auditoría descartada con logger sin configurar. Sin despliegue, sin OIDC, sin validación clínica. Siguiente: P5 (`docs/NEXT_PACKAGE.md`).
