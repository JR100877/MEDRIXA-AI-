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

## P5 — 2026-10-15 (Colombia), rama `dev/p5-postgres` (base `dev/p4b-http` @ 3c60ceb)

`PostgresStateRepository` (`app/persistence/postgres.py`): migración v1 idempotente, instantánea canónica en texto con SHA-256, tabla de eventos append-only forzada por triggers, CAS transaccional con `FOR UPDATE`, verificación completa en cada lectura, errores sin DSN. API: `MEDRIXA_PG_DSN` (secreto, excluyente con `MEDRIXA_STORE_DIR`). Arnés de pruebas con PostgreSQL 16 real efímero (`tests/persistence/pg_support.py`) y omisión explícita sin servidor; `crash_worker` admite DSN por variable de entorno. Dependencia opcional `requirements-postgres.txt` (psycopg 3.3.6). 27 pruebas nuevas; regresión 181/181. Sin base gestionada, sin datos reales, sin despliegue. Siguiente: P6 (`docs/NEXT_PACKAGE.md`).

## P6 — 2026-10-15 (Colombia), rama `dev/p6-leases` (base `dev/p5-postgres` @ e6022d5)

Puerto `RunLeases` (memoria, archivo, PostgreSQL con reloj de la base y migración v2 aditiva), `LeasedExecutor` con latido y guarda de escritura monotónica, recuperación que respeta arrendamientos vivos (`active_elsewhere`), CLI y API integradas (`MEDRIXA_LEASE_TTL_SECONDS`). Cambios aditivos: `set_write_guard` en `PersistentStateManager`, parámetro `leases` en `recover_interrupted_runs`, campo `active_elsewhere` (omitido cuando 0). Sin dependencias nuevas. 32 pruebas nuevas; regresión 213/213; 6/6 mutaciones detectadas. Siguiente: P7 (`docs/NEXT_PACKAGE.md`).

## P7 — 2026-10-16 (Colombia), rama `dev/p7-security` (base `dev/p6-leases` @ 5a6cd31)

Endurecimiento de seguridad en incrementos:
- **P7.0:** tipado estricto con mypy, cero hallazgos.
- **P7.1:** puerto de secretos con archivos montados `*_FILE`.
- **P7.2:** autenticador OIDC/JWT (PyJWT 2.15.1) con JWKS, rotación, refetch limitado y organización única (`MEDRIXA_AUTH_MODE=oidc`).
- **P7.3:** limitación de tasa por IP y por identidad, y tope de ejecuciones (429/503 con `Retry-After`).
- **P7.4:** PostgreSQL de mínimo privilegio (`pg_admin`, `MEDRIXA_PG_MIGRATE`, `SchemaNotReady`).
- **P7.5:** mediciones de latencia.
- **P7.6:** pool de conexiones psycopg-pool (`MEDRIXA_PG_POOL_SIZE`).
- **Documentación:** `docs/SECURITY.md` (límites de confianza, STRIDE, requisitos de despliegue, decisiones bloqueadas).
- **Auditoría de dependencias:** pip-audit, sin vulnerabilidades conocidas.

Contratos: los códigos de error `RATE_LIMITED` y `CAPACITY_EXCEEDED` y el 429 documentado son aditivos, y cambió la descripción del esquema de seguridad. Los contratos de dominio no cambiaron.

Se corrigieron dos fixtures de prueba intermitentes (firma "alterada" que podía coincidir con la real). Hay 92 pruebas nuevas y la regresión da 305 (1 omitida como root, verificada como usuario no privilegiado). Sin despliegue, sin IdP real, sin validación clínica. Siguiente: P8 (`docs/NEXT_PACKAGE.md`).

## P8 — 2026-10-16 (Colombia), rama `dev/p8-observability` (base `dev/p7-security` @ 19d279b)

- **P8.1:** puerto de métricas con registro en memoria acotado y exposición Prometheus. `/v1/metrics` exige el permiso nuevo `metrics:read`, solo para el operador.
- **P8.2:** sonda `/v1/ready` sin detalles, con ganchos opcionales `ping()` y `ready()`.
- **P8.3:** correlación por `request_id` en registros operativos JSON.
- **P8.4:** prueba de carga local reproducible. Su hallazgo: la E/S síncrona de PostgreSQL serializa el bucle de eventos (ADR-030).

Cambios aditivos: el código `NOT_READY`, `ReadyView` y dos rutas nuevas en OpenAPI. Sin dependencias nuevas. 21 pruebas nuevas; regresión 326 (1 omitida como root, verificada como no root). Siguiente: P9 (`docs/NEXT_PACKAGE.md`).
