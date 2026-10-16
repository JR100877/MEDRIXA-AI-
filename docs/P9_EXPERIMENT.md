# P9 — Experimento: E/S síncrona de PostgreSQL y bucle de eventos (opciones A, B y C)

**Estado: EXPERIMENTAL. No es la arquitectura definitiva de P9 y no se adoptó.** Rama `exp/p9-execution-offload`, derivada de `dev/p8-observability` @ 486f815. No hay fusiones, despliegues, servicios de pago ni proveedores externos. Autorización de Robinson del 2026-10-16 13:03 (MASTER_SPEC R30).

Procedencia sin cambios: `historical_code_recovered: false` y `code_origin: new_authorized_provisional`. Todo sigue siendo simulación con datos sintéticos y sin validación clínica.

## 1. Verificación previa

- **Remoto:** las 7 ramas publicadas coinciden con la genealogía de `CONTINUITY.json`. La rama predeterminada sigue siendo `provisional/2026-10-15` y no hay ninguna fusionada.
- **Manifiesto:** `evidence/SOURCE_MANIFEST.json` de P8 se verificó contra el árbol de P8 sin diferencias.
- **Suite de P8:** 326 pruebas, OK, 1 omitida como root. Esa prueba pasa como usuario no privilegiado.

## 2. Qué se cambió en el código

Es el mínimo necesario para medir. El comportamiento por defecto no cambia.

| Archivo | Cambio | Naturaleza |
|---|---|---|
| `app/web/service.py` | `RunService(..., execution=None)`. Si se pasa una estrategia, el cuerpo de la ejecución (con o sin `LeasedExecutor`) se le entrega como `work`. Con `None`, el código es el de P8. | Gancho aditivo interno |
| `app/web/app.py` | `create_app(..., execution=None)` lo pasa al servicio. | Gancho aditivo interno |
| `app/experimental/execution.py` | `ThreadLoopExecution(max_workers)`: un `ThreadPoolExecutor` acotado. Cada ejecución corre con `asyncio.run` en su propio bucle y con una copia de los contextvars. | Experimental, no conectado |
| `tests/experimental/` | 11 pruebas de caracterización: inline frente a C. | Nuevo |
| `scripts/experiments/` | Comparación de carga, perfil por ejecución y la suite completa con C forzada. | Nuevo |

No se tocó el núcleo (`app/core`), la seguridad (`app/security`, auth, policy), el modelo de datos, las migraciones, los esquemas de dominio ni el contrato HTTP. `contracts/http/openapi.v1.json` queda idéntico.

La estrategia no se configura por entorno: solo se activa pasándola por código. El parámetro `execution` de `create_app` es una API Python interna y aditiva. **Conservarlo si se adopta C requiere autorización.**

## 3. Opción C: compatibilidad

### 3.1 Suite existente con C forzada

`scripts/experiments/p9_suite_under_thread.py` sustituye `create_app` en el proceso para que toda app use `ThreadLoopExecution(16)`. Evidencia en `evidence/P9_suite_under_option_c.json`:

- **322 de 326 pruebas pasan** (0 fallos, 0 errores, 1 omitida como root).
- Se construyeron 60 apps con C, y 26 ejecutaron runs en hilos de trabajo.
- **4 pruebas se cuelgan** y se excluyen de forma explícita:
  - `web.test_idempotency…test_concurrent_duplicates_execute_once`
  - `web.test_idempotency…test_recovery_refused_while_a_run_executes_in_process`
  - `security.test_ratelimit…test_capacity_exhaustion_refuses_without_creating_runs`
  - `persistence.test_postgres…test_concurrent_duplicates_and_restart_replay`

**Causa común:** el agente de prueba espera un `asyncio.Event` creado en el bucle de la prueba. Con C, el agente corre en otro bucle. `Event.set()` desde el bucle original no es seguro entre hilos y no despierta al otro, así que la ejecución solo termina por el timeout cooperativo, o la prueba se cuelga antes. **No es un fallo de las garantías.** Es una restricción nueva del contrato de agentes: *un agente no puede compartir objetos ligados a un bucle (Event, Queue, Future, Lock de asyncio) con quien lo invoca*.

Los agentes de producción (`HistoryAgent`, `ClinicalAgent`) son puros y no se ven afectados.

### 3.2 Garantías verificadas de nuevo con coordinación segura entre hilos

Archivo: `tests/experimental/test_thread_execution.py`. Usa `threading.Event`, esperado con `asyncio.to_thread`.

| Garantía (P4b–P8) | Inline | C | Prueba |
|---|---|---|---|
| Duplicados concurrentes: una sola ejecución; los demás reciben `IDEMPOTENCY_IN_PROGRESS` | ✓ | ✓ | `test_concurrent_duplicates_execute_once` |
| Lo mismo en PostgreSQL con pool y arrendamientos, más reproducción tras reinicio sin reejecutar | ✓ | ✓ | `test_postgres_duplicates_execute_once_and_replay_after_restart` |
| `CAPACITY_EXCEEDED` sin crear la ejecución; `RUNS_ACTIVE` bloquea la recuperación | ✓ | ✓ | `test_capacity_and_recovery_guard_while_a_run_is_active` |
| `request_id` en los logs emitidos dentro del hilo de trabajo | ✓ | ✓ | `test_request_id_reaches_worker_thread_logs` |
| Hilos acotados a `max_active_runs`; el apagado espera las ejecuciones en curso | — | ✓ | `test_threads_are_bounded_and_shutdown_waits_for_in_flight_runs` |

### 3.3 Diferencias de comportamiento (medidas, no supuestas)

| Aspecto | Inline (P8) | Opción C | Prueba |
|---|---|---|---|
| Agente que espera un objeto asyncio del llamador | Funciona | Termina en `TIMEOUT` (o se cuelga sin timeout) | `test_agents_bound_to_the_caller_loop_break_under_option_c` |
| El cliente cancela la petición HTTP | La cancelación entra en la ejecución, que termina en revisión con `CANCELLED` | **La ejecución no se cancela**: termina normalmente y el reintento idempotente reproduce `REVIEW_REQUIRED` | `test_client_cancellation_semantics` |
| Plazas de capacidad tras cancelar | Coherentes (0) | **DEFECTO**: la plaza se libera mientras el hilo sigue ejecutando (`active_runs=0`, `in_flight=1`). Se admiten más ejecuciones de las que hay en curso, y la nueva queda encolada tras el hilo ocupado. | `test_capacity_slot_released_before_worker_finishes` |
| E/S bloqueante dentro de una ejecución | Bloquea `/v1/health` y a todos los clientes (> 0,5 s con 0,8 s de bloqueo) | `/v1/health` responde en < 0,2 s | `test_health_is_stalled_inline_but_not_with_option_c` |
| Latido del arrendamiento con el bucle HTTP bloqueado 1,5 s (TTL 1 s) | El arrendamiento expira y la ejecución se detiene (`CONFLICT`) | Se renueva en el bucle del hilo y la ejecución completa | `test_lease_heartbeat_survives_a_stalled_http_loop_only_with_option_c` |
| Apagado | Cancela las tareas | **Espera** a los hilos: no se pueden matar, el límite es el timeout cooperativo del agente | `test_threads_are_bounded_and_shutdown_waits_for_in_flight_runs` |

**Condiciones de carrera identificadas:**

1. **Contabilidad de capacidad al cancelar:** es el defecto de la tabla anterior. La corrección propuesta, no aplicada, consiste en liberar la plaza al terminar el trabajo del hilo y no al terminar la petición. Por ejemplo, con `add_done_callback` sobre el futuro del ejecutor, que mantiene la reserva hasta que el hilo acaba.
2. **Objetos ligados a un bucle:** compartirlos entre el llamador y el agente es una condición de carrera (sin despertar, o comportamiento indefinido). Se mitiga con una regla de contrato para los agentes y una prueba que la haga cumplir.
3. **Estado mutable compartido entre hilos:** las métricas (lock propio), el limitador (thread-safe, probado en P7), el pool de psycopg (thread-safe) y el repositorio en memoria (se probó concurrencia con hilos en P4) ya lo toleraban. El contador `_active` de `RunService` se modifica en el bucle HTTP, no en los hilos. **No se halló ninguna otra carrera** en la suite (322 pruebas) ni en las 11 de caracterización, repetidas 5 veces sin fallos.

## 4. Mediciones: inline (línea base P8) frente a C, en condiciones equivalentes

Herramienta: `scripts/experiments/p9_compare.py`.
- **Condiciones:** el mismo proceso, ASGI en proceso y agentes sintéticos. 400 peticiones por escenario y 3 repeticiones **intercaladas** (inline, C, inline, C…). Se informa la mediana; todas las repeticiones se conservan.
- **Además** se miden el retraso del bucle HTTP (un ticker de 5 ms), la latencia de sondas `/v1/health` lanzadas durante la carga, CPU/petición, hilos y RSS máximos.
- **Equipo:** 2 CPU, Python 3.13.16 y PostgreSQL 16.15 efímero por socket Unix.
- **Evidencia:** `evidence/P9_compare.json` (memoria y pool 4, la configuración de P8) y `evidence/P9_compare_pool16.json`.

Las sondas y el ticker añaden carga por igual a ambas estrategias, así que los valores absolutos no coinciden con `P8_load.json`. **La comparación válida es inline frente a C en la misma tabla.**

### 4.1 PostgreSQL, pool 4 (configuración por defecto de P8)

| Clientes / máx. activas | req/s inline → C | p50 ms | p99 ms | Retraso del bucle p99 ms | `/v1/health` p50 / p99 ms | CPU ms/pet. | Hilos máx. |
|---|---|---|---|---|---|---|---|
| 1 / 16 | 46,1 → 39,8 (**−14 %**) | 20,9 → 24,3 | 31,9 → 38,6 | 24,2 → **2,0** | 2,4/21,2 → 1,5/4,4 | 13,3 → 16,6 | 6 → 7 |
| 8 / 16 | 49,3 → 59,9 (**+21 %**) | 156 → 129 | 278 → 210 | 115 → **21** | 11,9/30,3 → 2,7/19,5 | 11,7 → 15,8 | 13 → 21 |
| 32 / 16 | 62,9 → 83,6 (**+33 %**) | 517 → 382 | 948 → 558 | 262 → **129** | 127/289 → 51/155 | 9,9 → 11,2 | 37 → 48 |
| 32 / 4 (saturación) | 170 → 394 (*) | 140 → 68 | 717 → 385 | 99 → **27** | 28/638 → 24/**42** | 4,5 → 2,5 | 37 → 41 |

(*) En saturación el req/s está dominado por rechazos 503 baratos. Con C se rechaza antes porque el bucle está libre: 201 = 56 inline frente a 12 con C, ya que los 400 intentos se agotan antes. Lo relevante aquí es el `/v1/health` p99 (638 → 42 ms): **C protege a los demás clientes durante la saturación**.

Dispersión entre repeticiones en c=32/16: inline [55,5; 62,9; 74,2] y C [83,8; 83,6; 72,8]. Las diferencias de c=8 y c=32 superan la dispersión; la de c=1 es estable (−14 %).

### 4.2 PostgreSQL, pool 16

| Clientes / máx. | req/s inline → C | Retraso del bucle p99 ms | `/v1/health` p50 ms |
|---|---|---|---|
| 1 / 16 | 46,6 → 40,3 (−14 %) | 26,4 → 2,1 | 2,4 → 1,5 |
| 8 / 16 | 55,9 → 60,2 (+8 %) | 136 → 23 | 13,9 → 4,8 |
| 32 / 16 | 67,3 → 70,6 (+5 %) | 275 → 126 | 114 → 100 |

**Un pool mayor no aumenta el techo con C** (pool 4: 83,6; pool 16: 70,6). El cuello de botella no son las conexiones.

### 4.3 Memoria (sin E/S)

| Clientes / máx. | req/s inline → C | CPU ms/pet. |
|---|---|---|
| 1 / 16 | 210,6 → 166,4 (**−21 %**) | 4,65 → 5,87 |
| 8 / 16 | 204,7 → 162,3 (**−21 %**) | 4,87 → 6,32 |
| 32 / 16 | 241,6 → 175,0 (**−28 %**) | 4,10 → 5,80 |

Sin E/S que liberar, C solo aporta su costo: crear un bucle por ejecución, el traspaso entre hilos y la contención del GIL.

### 4.4 Recursos

- **Hilos:** C añade como máximo `max_active_runs` hilos (máx. 16 + los existentes; medido +8 a +14).
- **RSS:** la diferencia por par intercalado es pequeña, de 1 a 9 MB. Nota: el RSS es del proceso y acumulativo entre escenarios (el almacén en memoria retiene ejecuciones), así que solo es comparable dentro de cada par.
- **CPU por petición:** con PostgreSQL, C usa **+13 % a +35 %** más CPU por petición.

### 4.5 Por qué el techo es ~60–85 req/s: perfil de una ejecución

`scripts/experiments/p9_profile_run.py` mide ejecuciones secuenciales inline con pool 4. Evidencia en `evidence/P9_run_profile.json`:

- **36 sentencias SQL y 9 commits por ejecución.** De ellas, 9 son la comprobación vacía del pool (`check_connection`, una por préstamo), 8 INSERT, 6 + 6 SELECT, 6 UPDATE de `medrixa_runs` y 1 UPDATE de arrendamiento.
- **CPU del proceso:** ~13 ms por ejecución sin perfilador (18 ms con cProfile), frente a ~26 ms de pared. psycopg concentra ~52 % del tiempo propio perfilado.
- **Cuenta de GIL:**
  - C con pool 4 y c=8: 59,9 req/s × 15,8 ms ≈ **0,95 s de CPU por segundo**. Es un núcleo saturado, que es el techo del GIL en un proceso.
  - Inline con c=8: 49,3 × 11,7 ms ≈ 0,58. El resto del tiempo el bucle espera E/S bloqueado.

**Conclusión:** C convierte la espera de E/S en trabajo útil hasta saturar el GIL. A partir de ahí, ni más hilos ni más conexiones ayudan. Para pasar el techo hay que **reducir el CPU por ejecución** (menos viajes y transacciones) o usar **varios procesos**.

## 5. Opción B: HTTP 202 + trabajador (solo diseño; no implementada)

El contrato `medrixa.http.v1` no cambia: `POST /v1/runs` sigue respondiendo 201 de forma síncrona.

**Contrato propuesto (a versionar):** una de estas dos variantes.
- **(a)** `Prefer: respond-async` sobre v1 (RFC 7240). El servidor responde `202 Accepted` con `Location: /v1/runs/{run_id}`, `Preference-Applied: respond-async`, `Retry-After` y un `RunView` en estado `created`. Sin la cabecera, todo se comporta como hoy.
- **(b)** Un `POST /v2/runs` siempre asíncrono, con OpenAPI v2 separado.

Con (a) se evita duplicar rutas. Con (b) el contrato queda más explícito. **La elección es de Robinson.**

**Piezas existentes que ya lo sostienen:**
- **Cola durable:** una ejecución en estado `created` persistida por el POST ya es un trabajo pendiente. La recuperación ya la trata aparte (`not_started`, P4) y nunca la marca `INTERRUPTED`.
- **Exclusión:** `RunLeases`, con época monotónica y guarda de escritura (P6). Un trabajador toma una ejecución `created` adquiriendo su arrendamiento. Con PostgreSQL, la selección sería `SELECT … WHERE status='created' … FOR UPDATE SKIP LOCKED` más la adquisición del arrendamiento, y necesitaría un índice nuevo por estado (migración v3 aditiva).
- **Idempotencia:** el `run_id` determinista no cambia. Un reintento del POST devuelve el estado actual: 202 si no ha terminado y el `RunView` final si ya terminó.
- **Sin reintento de agentes:** si una ejecución `running` pierde su trabajador, la recuperación la lleva a `INTERRUPTED`, igual que hoy.

**Piezas nuevas necesarias:**
- Un proceso trabajador supervisado (o varios). Esto rompe el techo del GIL de §4.5.
- Una identidad de servicio para el trabajador.
- Contrapresión: profundidad máxima de cola con 503 o 429 en vez de `CAPACITY_EXCEEDED` por proceso.
- Limitador y capacidad compartidos (hoy son por proceso).
- Una notificación opcional (`LISTEN/NOTIFY`) o el sondeo del cliente.
- Métricas de cola: profundidad y tiempo en cola.
- Auditoría de la toma y la finalización por parte del trabajador.
- La autorización se evalúa en el POST y el trabajador no la amplía.

**Riesgos:**
- Cambio de contrato y clientes que sondean.
- Más piezas que operar.
- Inanición o falta de equidad en la cola.
- Ejecuciones `created` huérfanas si el POST persiste y el proceso muere. Esto ya está cubierto: siguen en cola.
- Semántica de cancelación por el cliente. Habría que definir un `DELETE` o `cancel` explícito; hoy no existe.

**Cuándo conviene:** trabajos prolongados, como los futuros motores de conocimiento o LLM, cuya duración supera un timeout HTTP razonable. También cuando haga falta escalar a varios procesos.

## 6. Opción A: puerto de persistencia asíncrono (no implementada)

Requiere:
- un `AsyncStateRepository` con `AsyncConnection` y `AsyncConnectionPool` de psycopg;
- un `PersistentStateManager` y `Orchestrator` con `await` en cada transición;
- `RunLeases` asíncronos;
- una versión asíncrona de la suite de contrato del repositorio (30 pruebas) y de las de arrendamientos.

**Toca el núcleo estable**, lo que según la instrucción 6 exige autorización. Por eso no se prototipó.

**Lo que muestra el experimento:**
- A eliminaría el costo de hilos y bucles de C (+13 % a 35 % de CPU), pero **no** el CPU de psycopg por sentencia (36 por ejecución). El techo del GIL de §4.5 seguiría ahí: el beneficio esperado de rendimiento es parecido al de C, con un cambio mucho mayor.
- Las ventajas de A son otras: cancelación nativa (las tareas sí se cancelan) y la ausencia de la restricción de bucle para los agentes.
- Su riesgo es la regresión en el núcleo y en todos los adaptadores (memoria y archivo local tendrían que ofrecer las dos interfaces o envolverse).

## 7. Comparación técnica

| Criterio | A: persistencia asíncrona | B: 202 + trabajador | C: hilo + bucle por ejecución |
|---|---|---|---|
| Implementado en este experimento | No | No (solo diseño) | Sí, prototipo experimental |
| Desbloquea el bucle HTTP | Sí | Sí (HTTP no ejecuta) | **Sí, medido:** retraso p99 −80 % a c=8 |
| Rendimiento con PostgreSQL | ≈ C (estimado; el techo del GIL persiste) | Escala con procesos trabajadores (supera el GIL) | **+21 % a +33 %** a c=8–32; **−14 %** a c=1 |
| Rendimiento sin E/S (memoria) | Neutro | Neutro en HTTP | **−21 % a −28 %** |
| CPU por ejecución | Neutro o algo menor | Neutro, más el sondeo | **+13 % a +35 %** |
| Contrato HTTP | Sin cambios | **Cambia** (versionado) | Sin cambios |
| Núcleo, modelo de datos | **Cambia** el núcleo | Migración aditiva (índice) y trabajador | Sin cambios (gancho aditivo) |
| Compatibilidad de agentes | Total | Total | Restricción nueva: sin objetos asyncio compartidos (4 pruebas existentes dependían de ello) |
| Cancelación por el cliente | Nativa | Desacoplada (exige un endpoint de cancelación) | **No cancela**; reproducción idempotente |
| Latido del arrendamiento con el bucle bloqueado | Igual que hoy | Aislado en el trabajador | **Aislado** (medido) |
| Trabajos prolongados | No los resuelve (siguen en la petición) | **Sí** | No (siguen en la petición) |
| Costo del cambio y reversibilidad | Alto y difícil de revertir | Medio-alto, reversible tras versión | **Bajo y reversible** (un parámetro) |
| Defectos y riesgos abiertos | Regresión del núcleo | Operación de cola y clientes | Contabilidad de capacidad al cancelar (§3.3); apagado espera hilos |

## 8. Recomendación (para decisión de Robinson; nada está adoptado)

1. **No adoptar A ahora.** Tiene el costo más alto, toca el núcleo estable y, por el perfil de §4.5, no mejora el rendimiento de forma sustancial frente a C.
2. **C como mitigación de corto plazo, solo si se necesita capacidad de respuesta antes que B, y endurecida antes de adoptarla:**
   - liberar la plaza de capacidad al terminar el hilo (corrige el defecto de §3.3);
   - `max_workers = max_active_runs`;
   - fijar la regla de contrato de agentes (sin objetos asyncio compartidos) con una prueba que la haga cumplir;
   - documentar la semántica de cancelación (no cancela; la reproducción es idempotente);
   - apagado con plazo;
   - métrica de hilos en curso;
   - adaptar las 4 pruebas a coordinación segura entre hilos.

   Ventaja: aísla el latido del arrendamiento y protege a los demás clientes, con un cambio pequeño y reversible. Costo: más CPU por petición y peor rendimiento sin E/S. **No resuelve el techo ni los trabajos prolongados.**
3. **Atacar el techo medido de forma independiente**, reduciendo los viajes por ejecución: hoy son 9 transacciones, 36 sentencias y 9 comprobaciones del pool. Opciones: agrupar transiciones, revisar la política de `check` del pool (por ejemplo, comprobar solo tras un error) y leer sin revalidar dos veces. Toca la persistencia, así que **requiere autorización** y una nueva medición con `p9_compare`.
4. **B como arquitectura objetivo para trabajos prolongados y escalado a varios procesos**, cuando haya trabajos reales que lo justifiquen. Reutiliza `created` y los arrendamientos como cola durable. El componente `ThreadLoopExecution` de C sirve como trabajador en proceso de B. Antes de implementarla hacen falta decisiones de contrato (§5), del limitador y la capacidad compartidos y de la supervisión de procesos.

**Orden propuesto:** decidir entre C endurecida y esperar a B → reducir viajes (3) → diseñar el contrato de B → implementar B.

## 9. Limitaciones del experimento

- Hay un solo equipo de 2 CPU, sin red ni TLS, con ASGI en proceso y agentes sintéticos instantáneos. Con agentes lentos o red real, las proporciones cambian.
- PostgreSQL es local, efímero y accesible por socket Unix, con la latencia de E/S más baja posible. Con una base remota la E/S pesa más y **C ganaría más** frente a inline; no se midió.
- No se midieron varios procesos (uvicorn `--workers`), porque el limitador y la capacidad son por proceso.
- cProfile infla la sobrecarga de Python: las proporciones del perfil son indicativas.
- Hay variabilidad entre repeticiones (§4.1), y 3 repeticiones no bastan para intervalos de confianza formales.
- La opción A no se prototipó (toca el núcleo) y su estimación se basa en el perfil.
- La opción B no se implementó (por instrucción).
- La recuperación en el arranque y la cancelación por endpoint no existen en ninguna opción.

## 10. Decisiones pendientes (de Robinson)

1. Adoptar C endurecida como mitigación, o esperar a B.
2. Autorizar el trabajo de reducción de viajes y transacciones en la persistencia (toca un componente estable).
3. El contrato de B: `Prefer: respond-async` sobre v1 o una v2 separada, y la semántica de cancelación.
4. Si se quiere escalar a varios procesos: el limitador y la capacidad compartidos (PostgreSQL).
5. Si se fusionan las ramas P4–P8, y qué hacer con la rama experimental (conservarla como referencia o descartarla).

## 11. Reproducción

```bash
python -m unittest tests.experimental.test_thread_execution -v           # 11 pruebas
python -m scripts.experiments.p9_suite_under_thread --exclude-loop-bound  # suite con C forzada
python -m scripts.experiments.p9_compare --pg --repeats 3                 # memoria y pool 4
python -m scripts.experiments.p9_compare --pg --no-memory --pg-pool-sizes 16
python -m scripts.experiments.p9_profile_run --requests 200
```
