# Persistencia de ejecuciones (P4, P5)

Estado: el puerto está implementado y probado con dos adaptadores provisionales (P4) y con el **adaptador PostgreSQL (P5)**. Los tres pasan la misma suite de contrato, y PostgreSQL se probó contra un servidor PostgreSQL 16 real, efímero y local. No se ha usado ninguna base gestionada, compartida ni con datos reales. Solo datos sintéticos.

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

## Adaptador PostgreSQL — `PostgresStateRepository` (P5, `app/persistence/postgres.py`)

Requiere `psycopg` 3 (`requirements-postgres.txt`) y se importa solo cuando se usa. La API lo activa con `MEDRIXA_PG_DSN`, que se trata como secreto y es excluyente con `MEDRIXA_STORE_DIR`.

**Esquema.** La migración v1 es idempotente, no destructiva y está serializada con un advisory lock. Queda registrada en `medrixa_schema_migrations`.

| Tabla | Contenido |
|---|---|
| `medrixa_runs` | Una fila por ejecución. La instantánea se guarda como **texto JSON canónico** con `snapshot_sha256`, más columnas indexadas `case_id`, `revision` y `status`, con restricciones CHECK. No se usa JSONB porque renormaliza los números y rompería el checksum. |
| `medrixa_run_events` | Un evento por fila, PK `(run_id, sequence)`, FK a la ejecución con `ON DELETE RESTRICT`. **Solo admite añadir:** los triggers rechazan UPDATE, DELETE y TRUNCATE, también para el propietario mientras estén activos. |

**Escritura.** `save` se ejecuta en una sola transacción: `SELECT … FOR UPDATE` → verificación del registro → `check_successor` → `UPDATE … WHERE revision = esperada` → `INSERT` de los eventos nuevos. `create` usa `INSERT … ON CONFLICT DO NOTHING`, y 0 filas → `DuplicateRun`. Todos los valores van como parámetros; el SQL solo se compone con constantes.

**Lectura.** Verifica el checksum, que la instantánea sea canónica, el `State` completo, la coherencia de las columnas indexadas y **la coincidencia exacta con la tabla de eventos**. Cualquier diferencia → `CorruptRecord`, y el registro nunca se repara.

**Errores.** La base de datos inaccesible o con fallos da `StoreUnavailable`, sin el DSN ni el mensaje del driver. `repr` tampoco muestra el DSN.

**Pruebas** (`tests/persistence/test_postgres.py`), contra un clúster efímero creado por `tests/persistence/pg_support.py`: socket Unix privado, sin TCP, autenticación `peer` y un rol no superusuario.
- La suite de contrato de P4 sin cambios.
- Detección de manipulaciones y append-only real.
- 4 procesos concurrentes → 1 ganador.
- Recuperación, incluido un **proceso terminado con SIGKILL** con el agente en vuelo.
- Idempotencia HTTP con concurrencia y reinicio.
- Si no hay PostgreSQL, o con `MEDRIXA_TEST_PG=off`, las pruebas se **omiten** y nunca se cuentan como aprobadas. `MEDRIXA_TEST_PG_ADMIN_DSN` permite usar un servidor de pruebas existente.

**No incluido:**
- Pool de conexiones: hoy abre una conexión por operación.
- Configuración TLS.
- Aprovisionamiento de roles: se recomienda un rol de migración y un rol de aplicación con solo SELECT, INSERT y UPDATE en `medrixa_runs` y SELECT e INSERT en `medrixa_run_events`; no está probado.
- Respaldos, cifrado en reposo y retención.
- Driver asíncrono: la E/S bloquea el bucle de eventos de la API.

Una base gestionada o con costos requiere autorización.
