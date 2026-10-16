# P5 — siguiente paquete exacto (propuesta de planificación, no fase aprobada)

Base: P4b en la rama `dev/p4b-http`, publicada y sin fusionar. Ver `CONTINUITY.json`.

## Objetivo

Crear un adaptador PostgreSQL de `StateRepository` que pase **sin cambios** la suite `tests/persistence/test_repository_contract.py`, conservando los adaptadores provisionales y el comportamiento de P4 y P4b.

## Recursos verificados

PostgreSQL 16 ya está instalado en el entorno de desarrollo (`/usr/lib/postgresql/16`) y `psycopg` 3.3.6 está disponible en PyPI. Se usará un clúster efímero local, creado y destruido por las pruebas. No implica servicios gestionados, costos ni credenciales reales.

## Orden de ejecución

1. Esquema `runs` (instantánea JSONB, revisión, estado, caso) y `run_events` (PK `(run_id, sequence)`), con migración idempotente no destructiva.
2. `save`: una transacción con `UPDATE … WHERE run_id AND revision = esperada`; 0 filas → `RevisionConflict`. `check_successor` se valida en la aplicación con el registro bloqueado (`SELECT … FOR UPDATE`).
3. `create`: `INSERT`; una violación de PK → `DuplicateRun`.
4. Revalidar el JSONB en cada lectura: un registro inconsistente → `CorruptRecord`.
5. Ejecutar la suite de contrato, recuperación, idempotencia HTTP y concurrencia multiproceso contra PostgreSQL.
6. Documentar la configuración: DSN solo por entorno, nunca en el repositorio.

## Criterio de aceptación

La suite de contrato pasa idéntica en los tres adaptadores. La recuperación y la idempotencia HTTP funcionan con PostgreSQL. Si no hay servidor disponible, esas pruebas se **omiten de forma explícita** y nunca se declaran aprobadas. Sin datos reales ni infraestructura externa.

## Condiciones de aprobación

Requieren autorización de Robinson: fusionar cualquier rama, PostgreSQL gestionado o con costos, despliegue y migraciones destructivas.
