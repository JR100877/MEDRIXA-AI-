# Persistencia de ejecuciones (P4)

Estado: **puerto implementado y probado con dos adaptadores provisionales**. PostgreSQL sigue siendo el destino previsto y **no está implementado**. Solo datos sintéticos.

## Puerto `StateRepository` (`app/persistence/repository.py`)

| Operación | Contrato |
|---|---|
| `create(state)` | Solo ejecuciones nuevas (revisión 0, `created`, sin eventos). Un `run_id` existente → `DuplicateRun`; nunca sobrescribe. |
| `load(run_id)` | Devuelve `State` revalidado. Inexistente o identificador inválido → `RunNotFound`. Registro ilegible o inconsistente → `CorruptRecord`. |
| `save(state, expected_revision)` | Compare-and-swap. Revisión almacenada distinta de la esperada → `RevisionConflict` (subclase de `StateConflict`). El candidato debe ser **exactamente una transición** posterior, del mismo caso y ejecución, y extender sin reescribir eventos, mensajes y resultados; si no → `IntegrityViolation`. Una ejecución terminal es inmutable. |
| `scan()` / `list_runs(case_id)` | Índice de registros; los corruptos se informan como no legibles, sin lanzar ni modificarlos. |

Instantánea y eventos forman **un solo registro**: se escriben juntos o no se escriben.

`PersistentStateManager` (`app/persistence/manager.py`) aplica las mismas reglas que `StateManager` y **persiste antes de publicar** cada transición. Si la escritura falla, la memoria conserva la última revisión persistida. Un conflicto se propaga; el Orchestrator ya lo relanza sin convertirlo en revisión, de modo que nunca se sobrescribe a otro escritor.

## Adaptadores provisionales

### `InMemoryStateRepository` — `provisional-in-memory`

Para pruebas. No es duradero.

### `ProvisionalLocalFileRepository` — `provisional-local-file`

Para desarrollo y pruebas de reinicio en un solo equipo.

- **Un archivo JSON por ejecución.** El nombre es el SHA-256 del `run_id`, lo que evita rutas peligrosas y caracteres inválidos. Permisos 0600 en POSIX.
- **Sobre `medrixa.local-run-record.v1`.** Contiene formato, adaptador, run, caso, revisión, estado, `state_sha256` e instantánea. Al leer se verifica todo: claves exactas, formato, checksum, revalidación completa de `State`, coherencia sobre/estado y que el registro corresponda al nombre del archivo.
- **Escritura atómica.** Se escribe un temporal, se hace fsync, se aplica `os.replace` y se hace fsync del directorio. Una caída deja el registro anterior completo o el nuevo completo, nunca uno parcial.
- **Bloqueo exclusivo por ejecución con archivo de bloqueo del SO.** Usa `fcntl.flock` en POSIX y `msvcrt.locking` en Windows. Windows **no está probado**.
- **Checksum, no firma.** El SHA-256 detecta corrupción accidental, no manipulación: quien pueda escribir el directorio puede falsificar un registro.
- **Exclusiones.** Sin cifrado en reposo, sistemas de archivos de red, bloqueo multi-servidor ni retención.

## Recuperación (`app/persistence/recovery.py`)

`recover_interrupted_runs(repository)` se ejecuta al arrancar, sin ejecutores activos. También está disponible por CLI: `python -m app --store DIR --recover`.

| Estado almacenado | Acción |
|---|---|
| `running`, con o sin solicitud pendiente | `awaiting_human_review` con `INTERRUPTED`. Se conserva todo el historial. **No se reintenta ni se reejecuta ningún agente**, no se supone idempotencia y un resultado tardío ya no puede aceptarse. |
| `created` (nunca despachó) | Sin cambios; se informa en `not_started` para que una persona decida. |
| `awaiting_human_review` | Sin cambios. |
| Registro corrupto | Se cuenta; nunca se modifica, repara ni borra. |

La recuperación es idempotente. Si hay recuperadores concurrentes, cada ejecución recibe una sola transición, gracias al CAS. **Límite:** no hay arrendamiento ni latido. Recuperar una ejecución que otro proceso vivo aún ejecuta hace fallar a ese proceso con `RevisionConflict`. Es seguro, pero disruptivo.

## Destino PostgreSQL (diseño, no implementación)

Este es un esquema orientativo para el futuro adaptador. Debe pasar la misma suite `tests/persistence/test_repository_contract.py`.

- `runs(run_id PK, case_id, revision, status, snapshot JSONB, updated_at)` y `run_events(run_id, sequence, kind, at, message_id, reason, PRIMARY KEY(run_id, sequence))`.
- `save`: una transacción con `UPDATE runs SET … WHERE run_id=$1 AND revision=$2`. Si afecta 0 filas → `RevisionConflict`. Le sigue el `INSERT` del nuevo evento, con la PK de eventos como segunda barrera contra duplicados.
- `create`: `INSERT`; una violación de PK → `DuplicateRun`.

Pendientes antes de implementarlo: migraciones, credenciales gestionadas, TLS, roles de mínimo privilegio, cifrado y respaldo. Requiere aprobación si implica costos o infraestructura.
