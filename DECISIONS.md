# Decisiones de ingeniería

## ADR-001 — Área aislada y contratos nuevos

No se recuperó fuente histórica. Se conserva el diseño y se crea la rama `provisional/2026-10-15`, sin asignar versión histórica. Los contratos `medrixa.prototype.v1` son nuevos y deben compararse antes de cualquier migración futura. Tener ambos archivos `state.py` (datos) y `state_manager.py` (transiciones) es una separación provisional explícita, no una reconciliación histórica demostrada.

## ADR-002 — Python y Pydantic disponibles

Python 3.12.14 y Pydantic 2.13.5 están disponibles. Se fija esta dependencia para reproducibilidad. No se instalaron paquetes. Se usa `unittest` de la biblioteca estándar porque pytest no está disponible; las pruebas escritas como TestCase también podrán ejecutarse con pytest cuando se habilite. La elección no sustituye FastAPI/PostgreSQL/pgvector previstos.

## ADR-003 — Seguridad por restricción de alcance

Solo datos sintéticos, contratos sin campos de identificación directa, reglas técnicas sin inferencia clínica y revisión humana terminal obligatoria. La marca `synthetic=True` no detecta PHI ni prueba anonimización. No introducir datos reales, incluso marcados como sintéticos. Identificadores y códigos también deben ser sintéticos. La auditoría contiene identificadores opacos y códigos, no valores de observaciones.

## ADR-004 — Estado local y atómico

RLock para modificaciones en un proceso, revisión esperada para evitar actualizaciones perdidas y validación completa antes de publicar la nueva instantánea. No es una transacción distribuida ni almacenamiento duradero. El repositorio sí se conserva mediante Git bundle y paquete persistente.

## Fuentes técnicas consultadas

- Pydantic, modelos y validación estricta: https://github.com/pydantic/pydantic/blob/main/docs/concepts/models.md y https://github.com/pydantic/pydantic/blob/main/docs/concepts/strict_mode.md (consulta 2026-10-15). Justifican revalidar entradas y evitar considerar model_copy/model_construct una validación.
- Python asyncio: https://docs.python.org/3/library/asyncio-task.html (consulta 2026-10-15). Para el siguiente paquete, los tiempos de espera serán cooperativos; no se prometerá detener código bloqueante o no confiable.

## ADR-005 — Ejecución asíncrona acotada

Agentes locales explícitos, dos pasos por defecto, sin descubrimiento de plugins, red ni reintentos automáticos. No se reintentan efectos no idempotentes. Cada ejecución posee un StateManager exclusivo; no se permite reiniciar una instancia usada. Las excepciones de agentes se convierten en códigos sin copiar mensajes al estado. CancelledError se registra y se propaga. Los timeouts de asyncio son cooperativos, no sandbox ni límite duro.

## ADR-006 — Marcadores y frontera JSON

La igualdad de Literal[True] admitía enteros en P1/P2. P3 exige booleano exacto antes de validar, y exige procedencia explícita en ClinicalCase. Se endurece este contrato nuevo antes de publicarlo; no se cambia una API histórica. Frontera de entrada local de 1 MiB, duplicados rechazados y errores uniformes.

## ADR-007 — Trazabilidad estructural y revisión final

State revalida la secuencia de eventos contra mensajes y resultados. No se llama auditoría inviolable: falta firma y persistencia externa. La CLI solo imprime contadores, no observaciones ni IDs. La comparación del tiempo monotónico rechaza respuestas tardías; no resuelve bloqueo infinito ni procesos hostiles.

## ADR-008 — Puerto StateRepository y adaptadores provisionales (P4)

La persistencia se define como puerto con contrato probado (`tests/persistence/test_repository_contract.py`), independiente del motor. Se implementan dos adaptadores **provisionales**: en memoria (pruebas) y archivo local (desarrollo y reinicio). PostgreSQL sigue siendo el destino histórico previsto; no se implementa ni se declara implementado. No se añadieron dependencias. Cada `save` es un compare-and-swap de exactamente una transición que extiende el historial; instantánea y eventos son un único registro atómico. Detalle: `docs/PERSISTENCE.md`.

## ADR-009 — Recuperación fail-closed y código `INTERRUPTED`

Toda ejecución `running` hallada tras un reinicio pasa a revisión humana con el nuevo `StopCode` `INTERRUPTED`; no se reanuda ni se reintenta ningún agente, porque su efecto y resultado son desconocidos y no se supone idempotencia. Las ejecuciones `created` se informan sin tocarlas. El código `INTERRUPTED` es un cambio **aditivo** del contrato `medrixa.prototype.v1`: todo documento válido en P3 sigue siendo válido (`tests/persistence/test_contract_compat.py`); se regeneraron los esquemas State, Plan y RunSummary. No hay arrendamiento/latido: la recuperación debe ejecutarse sin ejecutores activos.

## ADR-010 — Persistir antes de publicar

`StateManager._commit` delega en un gancho `_publish` (comportamiento P1–P3 idéntico). `PersistentStateManager` lo sobrescribe para escribir en el repositorio antes de actualizar memoria: un fallo de almacenamiento no deja estado en memoria por delante del almacenado. Las 52 pruebas previas pasan sin modificación.

## ADR-011 — Adaptador HTTP FastAPI local sobre un servicio independiente del transporte (P4b)

`RunService` (`app/web/service.py`) concentra autorización, idempotencia, auditoría y ejecución; FastAPI solo traduce HTTP. Contratos HTTP versionados `medrixa.http.v1` con base estricta propia (sin `schema_version` de dominio). El cuerpo se lee con límite propio y se valida con el mismo parser estricto de P3 (`parse_strict`, refactor sin cambio de comportamiento). Dependencias nuevas fijadas: fastapi 0.143.2 (runtime), httpx 0.28.1, uvicorn 0.53.0 y ruff 0.16.8 (desarrollo). Sin perfil productivo.

## ADR-012 — Idempotencia por run_id determinista

`run_id = idem-SHA256(dominio|sujeto|clave)`. Reutiliza la creación atómica de `StateRepository` (P4) como barrera de duplicados: duradera entre reinicios y procesos sin almacén de claves adicional. `PersistentStateManager` recibe `run_id` opcional (aditivo). La huella de la petición es el `ClinicalCase` completo, por eso `case_id` es obligatorio en HTTP. Ejecuciones no terminadas responden 409 y nunca se reejecutan; tras recuperación se reproducen con `INTERRUPTED`.

## ADR-013 — Autenticación por puerto; token HMAC provisional

La API depende de `Authenticator`/`Principal`. El adaptador HMAC local (secreto compartido ≥32 bytes, sin valor por defecto) verifica firma en tiempo constante, emisor, audiencia, expiración, vida máxima y claims estrictos. No es un proveedor de identidad: sin revocación ni rotación. Producción requiere OIDC/IdP (pendiente, P7).

## ADR-014 — Autorización mínima con denegación por defecto y 404 en accesos cruzados

Tres roles explícitos; ningún permiso de aprobación clínica existe. Permisos con alcance por caso; lecturas fuera de alcance responden igual que un recurso inexistente. El operador recupera pero no lee ejecuciones.

## ADR-015 — Errores uniformes y auditoría de accesos que falla cerrada

Catálogo fijo de códigos y mensajes; manejadores propios reemplazan el 422 de FastAPI (que repite la entrada). La auditoría de mutaciones ocurre antes de los efectos; si el sink no puede registrar (incluido un logger sin configurar, hallado en la prueba con servidor real), la operación se rechaza con 503.

## ADR-016 — Adaptador PostgreSQL con instantánea canónica en texto y eventos append-only (P5)

`PostgresStateRepository` implementa el puerto sin cambiarlo y pasa la suite de contrato de P4 sin modificaciones. La instantánea se guarda como texto JSON canónico con SHA-256 (JSONB renormaliza números); columnas indexadas y tabla de eventos se verifican contra la instantánea en cada lectura. Los eventos son append-only en la base (triggers contra UPDATE/DELETE/TRUNCATE), mejora respecto al adaptador de archivos. Migración v1 idempotente, no destructiva, bajo advisory lock. `psycopg` 3.3.6 es dependencia opcional; el núcleo no la importa. Sin pool de conexiones ni driver asíncrono (pendiente).

## ADR-017 — Pruebas contra PostgreSQL real efímero, nunca simulado

Las pruebas crean un clúster local temporal (socket Unix privado, sin TCP, autenticación `peer`, rol no superusuario) y una base nueva por prueba; se destruye al salir. Sin servidor disponible las pruebas se omiten explícitamente. No se usan bases compartidas, gestionadas ni con datos reales.

## ADR-018 — Arrendamientos para vivacidad, no para seguridad (P6)

La seguridad frente a ejecutores obsoletos ya la garantizan el CAS y la inmutabilidad terminal (P4). Los arrendamientos solo permiten que la recuperación conviva con ejecutores vivos de otros procesos. Por eso viven en un puerto propio (`RunLeases`) y `StateRepository` no cambia. Un arrendamiento vencido nunca revive; los adaptadores PostgreSQL usan el reloj de la base. `PersistentStateManager` recibe una guarda de escritura opcional (aditivo) que el ejecutor usa con un plazo monotónico local, de modo que deja de escribir antes de que su arrendamiento pueda vencer en otra parte.

## ADR-019 — Campo aditivo `active_elsewhere` omitido cuando es 0

`RecoveryReport`, `RecoverySummary` (CLI) y `RecoveryView` (HTTP) ganan `active_elsewhere`. En las salidas públicas se omite cuando vale 0, de modo que las respuestas v1 existentes no cambian; los esquemas lo declaran opcional con valor por defecto 0. Esquemas y OpenAPI regenerados; diferencias verificadas como puramente aditivas. TTL por defecto 30 s y latido TTL/3 son valores provisionales pendientes de aprobación de Robinson.

## ADR-020 — Tipado estricto como puerta de calidad (P7.0)

Se adopta mypy 2.4.0 con el plugin de pydantic y `check_untyped_defs`, con cero hallazgos sobre `app/`. Los 21 hallazgos iniciales eran tipos débiles (literales como `str`, `None` sin estrechar) y se corrigieron sin cambiar comportamiento. Configuración en `mypy.ini`.

## ADR-021 — Secretos por puerto, con archivos montados como vía recomendada (P7.1)

`SecretProvider` resuelve `MEDRIXA_<X>` o `MEDRIXA_<X>_FILE`, nunca ambos. Los archivos deben ser regulares, ≤64 KiB, UTF-8 y no escribibles por grupo u otros; se admiten los enlaces simbólicos de Kubernetes. Los errores nombran la variable, nunca el valor. No se incluye ningún gestor externo porque su elección está bloqueada; el puerto permite añadirlo.

## ADR-022 — OIDC por el puerto existente, verificación delegada en PyJWT (P7.2)

No se escribe criptografía propia. MEDRIXA decide qué clave y qué comprobaciones aplicar:
- Algoritmos asimétricos en lista permitida, lo que hace imposible la confusión con HMAC.
- `kid` obligatorio y `alg` de la JWK fijado.
- iss/aud/exp/iat/sub obligatorios, con tolerancia y vida acotadas.
- JWKS solo por HTTPS (HTTP solo en loopback, para infraestructura de prueba), sin redirecciones, con refetch limitado y obsolescencia acotada.

Los sujetos no válidos como `Identifier` (como `auth0|…`) se mapean a `sub-<sha256(iss,sub)>`. La autenticación se ejecuta fuera del bucle de eventos. **Organización única:** el claim de organización debe coincidir con `MEDRIXA_ORGANIZATION_ID`. Multi-tenancy queda bloqueado (docs/SECURITY.md §4).

## ADR-023 — Limitación de tasa en dos capas y capacidad reservada antes de crear (P7.3)

El límite por IP se aplica antes de autenticar, para frenar la fuerza bruta, y el límite por identidad después. El token bucket tiene memoria acotada (LRU). El tope de ejecuciones se reserva **antes** de crear la ejecución, de modo que la saturación nunca deja ejecuciones huérfanas en `created`. Los códigos `RATE_LIMITED` (429) y `CAPACITY_EXCEEDED` (503) llevan `Retry-After` y son aditivos. Los límites son por proceso; con varias réplicas hace falta un limitador compartido.

## ADR-024 — PostgreSQL de mínimo privilegio: el dueño migra, la aplicación solo opera (P7.4)

`pg_admin migrate/grant` con `MEDRIXA_PG_ADMIN_DSN[_FILE]`. El rol de aplicación tiene exactamente: runs SELECT/INSERT/UPDATE, events SELECT/INSERT, leases SELECT/INSERT/UPDATE y migrations SELECT. Al no ser dueño, no puede desactivar los triggers append-only, lo que cierra el riesgo de P5 para el rol de aplicación. Con `MEDRIXA_PG_MIGRATE=false`, la versión de esquema se verifica y falla cerrada (`SchemaNotReady`).

## ADR-025 — Pool de conexiones justificado por medición (P7.6)

Medido en este equipo (`evidence/P7_latency.json`): abrir una conexión costaba unos 3,1 ms de cada operación de ~4,7 ms, incluso por socket Unix. Se adopta psycopg-pool 3.3.3 (proyecto oficial de psycopg):
- Es opcional por repositorio (`pool_size`), lo comparten ejecuciones y arrendamientos, y comprueba cada conexión antes de entregarla.
- Se cierra al apagar la app. `MEDRIXA_PG_POOL_SIZE` vale 4 por defecto y 0 lo desactiva.
- Resultado: latencia mediana entre 74 % y 91 % menor.

## ADR-026 — TTL de arrendamiento de 30 s, aún provisional, ahora con datos

La renovación tarda ≤~11 ms en p99 sin pool y ~1 ms con pool, frente a un latido cada 10 s. El margen es de unas mil veces, así que 30 s no se cambia. El valor definitivo depende de la operación real y sigue pendiente de la decisión de Robinson.

## ADR-027 — Métricas por puerto propio con exposición Prometheus; OpenTelemetry como adaptador futuro (P8.1)

FastAPI 0.143 arrastra `opentelemetry-api`, pero sin SDK es un no-op y exportar exige SDK y exportador, que es una decisión de despliegue. Se adopta un puerto `Metrics` con registro en memoria y exposición en texto Prometheus 0.0.4, **sin dependencias nuevas**:
- **Métricas declaradas de antemano** con etiquetas acotadas: plantillas de ruta, códigos y razones enumeradas. **Nunca** identificadores de caso, ejecución o sujeto.
- **Memoria acotada** (`max_series`), y el registro nunca propaga excepciones.
- **Exposición:** `GET /v1/metrics` requiere el permiso nuevo `metrics:read`, solo para el operador.
- **OpenTelemetry:** un adaptador sobre el mismo puerto queda PROPUESTO.

## ADR-028 — Sonda de preparación pública y sin detalles (P8.2)

`GET /v1/ready` es pública, igual que `/v1/health`, porque la usan las sondas de orquestación. Responde 200 o 503 `NOT_READY` con `Retry-After` y mensaje constante, sin revelar qué falló. Comprueba el almacén (`ping()`: directorio escribible, o base alcanzable con esquema migrado), las claves de verificación (JWKS en OIDC) y que la auditoría esté configurada. Los ganchos `ping()` y `ready()` no son abstractos, así que los adaptadores de terceros no se rompen.

## ADR-029 — Correlación por `request_id` mediante una variable de contexto (P8.3)

El middleware fija el `request_id` y siempre lo restablece en `finally`. Un filtro de logging lo añade a todo el árbol `medrixa.*`, incluso en el hilo de autenticación. Los registros operativos salen en líneas JSON con mensajes constantes y no se mezclan con la auditoría.

## ADR-030 — La E/S síncrona de la base serializa el bucle de eventos: medido, a resolver en P9 (P8.4)

Prueba de carga local (`evidence/P8_load.json`): con PostgreSQL y pool, el rendimiento se queda en ~45–48 req/s entre 1 y 8 clientes, y la latencia p50 crece de forma lineal (20 → 179 ms). En memoria escala de 188 a 352 req/s. La causa son las llamadas síncronas de psycopg dentro de la ejecución asíncrona. No se corrige en P8 porque exige separar la ejecución del ciclo HTTP o hacer asíncrono el puerto de persistencia, un cambio de arquitectura que debe preservar la idempotencia, el fencing y el no reintento. Es la prioridad técnica de P9. `MEDRIXA_MAX_ACTIVE_RUNS=16` se mantiene: en la medición protege como se diseñó (rechaza con 503 en vez de degradar a todos).

## ADR-031 — Experimento P9: hilo + bucle propio por ejecución (opción C) medido; no adoptado (PROPUESTO)

Rama `exp/p9-execution-offload`. Detalle completo y reproducción en `docs/P9_EXPERIMENT.md`. Se añadió un gancho aditivo `execution` (por defecto `None`, comportamiento de P8 intacto) y un ejecutor experimental `ThreadLoopExecution`. Hallazgos:
- **Bucle HTTP desbloqueado.** Con PostgreSQL y c=8, el retraso p99 del bucle baja de 115 a 21 ms y `/v1/health` p50 de 11,9 a 2,7 ms. En saturación, `/v1/health` p99 baja de 638 a 42 ms. El latido del arrendamiento sobrevive a un bucle HTTP bloqueado.
- **Rendimiento con PostgreSQL:** +21 % (c=8) y +33 % (c=32) con pool 4, pero −14 % con c=1. Sin E/S (memoria) es −21 % a −28 %. El CPU por petición sube entre 13 % y 35 %.
- **Techo de ~60–85 req/s por CPU y GIL, no por E/S ni por conexiones.** Un pool de 16 no lo sube. Cada ejecución hace 36 sentencias, 9 commits y 9 comprobaciones del pool, con ~13 ms de CPU.
- **Compatibilidad:** 322 de 326 pruebas pasan con C forzada. Las 4 restantes comparten un `asyncio.Event` del bucle llamador con el agente. Esto impone una regla nueva de contrato de agentes; los de producción no se ven afectados.
- **Semántica distinta:** cancelar la petición no cancela la ejecución (el reintento la reproduce) y el apagado espera a los hilos.
- **Defecto hallado:** la plaza de capacidad se libera al cancelar mientras el hilo sigue en curso.

Recomendación, pendiente de Robinson: no hacer A ahora. C solo endurecida y como mitigación. Reducir viajes por ejecución con autorización. B como arquitectura objetivo para trabajos prolongados y varios procesos.
