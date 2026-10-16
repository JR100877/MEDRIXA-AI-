# Estado de pruebas

P1 — comando: `python -m unittest tests.core.test_state -v`

Entorno: Python 3.12.14, Pydantic 2.13.5. Resultado observado: 17 pruebas, 0 errores, 0 fallos, 0 omitidas; 0.007 s. Verifica validación de contratos, rechazo de datos marcados como reales, inmutabilidad, serialización, correlación, atomicidad, concurrencia y aislamiento.

No es validación clínica, evaluación de seguridad productiva ni prueba de conformidad FHIR. No se ejecutó pytest porque no está instalado.

P2 — `python -m unittest tests.test_agents tests.core.test_engine -v`: 22 pruebas aprobadas, 0 errores/fallos/omitidas; 0.049 s. Evidencia literal: `evidence/P2_tests.txt`. P1 no se repitió porque este paquete no cambió sus fuentes.

## P3 — cierre verificado

Pruebas focalizadas: `python -m unittest tests.test_boundary -v` — 13 aprobadas, 0 fallos, 0 errores.

Regresión justificada: P3 cambió validación de contratos, consistencia de State y límites de Orchestrator; se ejecutó una vez la suite completa `python -m unittest discover -s tests -t . -v`. Resultado final: **52 pruebas, 0 fallos, 0 errores, 0 omitidas**, 0.490 s. Evidencia: `evidence/P3_regression.txt`.

Demo ejecutada: `python -m app`; resultado en `evidence/demo_summary.json`: dos agentes completados, seis eventos y revisión humana obligatoria. También se ejecutaron ambos archivos JSON de ejemplo como parte de pruebas CLI.

Defecto encontrado y corregido: Pydantic Literal[True] aceptaba 1/1.0 por igualdad; validadores previos exigen booleano True y procedencia explícita. Se probaron además resultados tardíos de agentes que suprimen cancelación y snapshots con eventos inconsistentes.

Los esquemas son generados desde los modelos, no una certificación de interoperabilidad. No se midió cobertura ni se ejecutó análisis estático de tipos; no se atribuyen porcentajes de cobertura.

## P4 — persistencia y recuperación (rama `dev/p4-persistence`)

Entorno: Python **3.13.16**, Pydantic 2.13.5, Linux (`evidence/P4_environment.json`). P1–P3 se verificaron en 3.12.14; 3.12 no se volvió a probar en esta sesión.

Pruebas focalizadas: `python -m unittest tests.persistence.test_repository_contract tests.persistence.test_local_file tests.persistence.test_recovery tests.persistence.test_contract_compat -v` — **55 aprobadas**, 0 fallos/errores/omitidas. Evidencia: `evidence/P4_tests.txt`.

- Contrato del repositorio (15 pruebas × 2 adaptadores): creación/duplicado, identificadores inválidos, revisión obsoleta, una transición por guardado, reescritura de historial, escritura de otro caso, terminal inmutable, **8 escritores concurrentes → 1 ganador**, aislamiento con 6 casos concurrentes, persistir antes de publicar, resultado nunca aceptado dos veces tras reanudar, fallo de escritura sin avance en memoria, conflicto con escritor externo sin sobrescritura.
- Adaptador de archivos (13): registro truncado, checksum, estado inconsistente con checksum válido, sobre/estado discrepantes, formato desconocido, clave extra, UTF-8 inválido, registro copiado a otra ejecución, tamaño excesivo, fallo de `os.replace` sin pérdida ni temporales, temporales ajenos ignorados, permisos 0600, **4 procesos concurrentes → 1 ganador**.
- Recuperación (10): política por estado, historial preservado, resultado tardío rechazado, idempotencia byte a byte, corrupto intacto, 4 recuperadores concurrentes, CLI y **reinicio real**: una ejecución completa y otra terminada con SIGKILL con el agente en vuelo; un proceso nuevo ejecuta `--recover` → completa intacta (6 eventos), interrumpida en revisión `INTERRUPTED` con sus eventos previos, 0 resultados y 0 agentes reejecutados.
- Compatibilidad (2): `StopCode` solo se amplía con `INTERRUPTED`; los 10 esquemas publicados coinciden con los modelos.

Regresión justificada (P4 modificó `state_manager.py`, `contracts.py` y la CLI): `python -m unittest discover -s tests -t . -v` — **107 pruebas, 0 fallos, 0 errores, 0 omitidas** (52 previas sin modificar + 55 nuevas). Evidencia: `evidence/P4_regression.txt`. Repetida 8 veces sin fallos intermitentes y una vez con `-W error`.

Verificación de las pruebas: se introdujeron 4 defectos deliberados (sin control de revisión, sin rechazo de reescritura, publicar antes de persistir, reanudar en vez de revisar); la suite falló en los cuatro y pasó al restaurar el código.

Demo `python -m app` sin cambios respecto a P3 (`evidence/demo_summary.json`). No es validación clínica ni prueba de PostgreSQL; no se midió cobertura.

## P4b — API HTTP local (rama `dev/p4b-http`)

Entorno: Python 3.13.16, Pydantic 2.13.5, FastAPI 0.143.2, Starlette 1.6.0, httpx 0.28.1 (`evidence/P4b_environment.json`).

Pruebas focalizadas: `python -m unittest discover -s tests/web -t . -v` — **47 aprobadas**, 0 fallos/errores/omitidas. Evidencia: `evidence/P4b_tests.txt`.

- **Autenticación, configuración y política (10).** Cubren el token válido y 13 variantes de cabecera o firma inválidas con fallo uniforme, y 12 claims inválidos: expirado, emitido en el futuro, vida excesiva, audiencia, emisor, rol desconocido, claim extra, sujeto inválido y tiempos como texto. También la tolerancia de reloj, el rechazo de `production` y de secretos débiles sin repetirlos, el secreto oculto en el volcado, los hosts comodín, `/docs` solo en desarrollo y la matriz de roles con denegación por defecto.
- **Contrato HTTP (10).** Cubren la forma de las vistas sin observaciones, `Location` y lectura de ejecución y eventos. Hay 11 entradas inválidas que responden 400 sin repetir la entrada; también 415, 413 por `Content-Length`, por flujo sin longitud y por longitud negativa, 404, 405 y host no permitido. Se prueban las cabeceras de seguridad, el `X-Request-ID` sin inyección, OpenAPI publicado igual al generado, el sobre de error ante la validación propia de FastAPI y `health`.
- **Autorización (7).** Ninguna ruta protegida funciona sin token válido o expirado; el 401 llega antes de validar el cuerpo. Se prueban la creación por rol y caso, la lectura cruzada idéntica a la inexistente (también con traversal y longitud excesiva), el revisor, el operador y la auditoría de identidad sin tokens, secretos ni observaciones.
- **Idempotencia (10).** Cubren la clave obligatoria, la repetición idéntica sin reejecutar, el cuerpo distinto (422), el alcance por sujeto, la duración entre reinicios con almacén en disco y la ejecución interrumpida (409 → recuperación → repetición `INTERRUPTED`). Las pruebas **concurrentes reales** (httpx.AsyncClient + ASGITransport) verifican: 10 duplicados con el agente en vuelo → 1 ejecución y 9 `IDEMPOTENCY_IN_PROGRESS`; 10 primeras peticiones simultáneas → 1 ganadora; claves distintas en paralelo; y recuperación rechazada (`RUNS_ACTIVE`) con una ejecución activa.
- **Adversas (10).** Prueban el fallo de almacén (503 sin detalles, sin reejecutar al reintentar) y el fallo de auditoría (503 antes de cualquier efecto). Un logger de auditoría sin configurar falla cerrado y uno configurado emite JSON. También cubren el registro corrupto (500 `STORE_INTEGRITY_ERROR`), la excepción inesperada (500 genérico sin rutas ni mensajes), el fallo de agente (201 en revisión `AGENT_ERROR`), la auditoría del resultado fallido, el reloj adelantado y la CLI `app.web`, que emite tokens válidos y nunca imprime el secreto.

Regresión justificada (P4b tocó `api/contracts.py` y `persistence/manager.py`): `python -m unittest discover -s tests -t . -v` da **154 pruebas, 0 fallos, 0 errores, 0 omitidas** (107 previas sin modificar en su lógica + 47 nuevas). Evidencia: `evidence/P4b_regression.txt`.
- Se repitió 5 veces sin intermitencias.
- También se ejecutó con todas las advertencias como errores, salvo una de terceros: el alias `anyio.abc.BlockingPortal`, que importa el `TestClient` de Starlette.
- Las 47 pruebas web pasan tanto con `httpx2` (preinstalado y preferido por Starlette) como con `httpx` 0.28.1.

**Verificación de las pruebas:** se introdujeron 8 defectos deliberados (firma no verificada, sin expiración, lectura sin alcance por caso, idempotencia sin comparar el cuerpo, repetición de ejecución no terminada, auditoría posterior a los efectos, operador con lectura y manejador de validación con eco). La suite detectó los 8; el octavo, después de agregar una prueba de protección.

**Análisis estático:** ruff 0.16.8 con reglas E, F, W, B y S (bandit) sobre `app/` y `scripts/` no dejó hallazgos. Hubo 2 falsos positivos S105, anotados con su justificación. En `tests/persistence` y `tests/web` tampoco quedan hallazgos. Quedan 3 imports sin uso en pruebas de P1–P3; no se tocaron porque son pruebas protegidas y el cambio sería cosmético.

**Servidor real:** `python -m app.web serve` en 127.0.0.1, probado con curl, dio 201, una repetición idéntica marcada `Idempotent-Replayed: true`, 401 sin token y ninguna cabecera `Server`. La auditoría salió como líneas JSON sin secreto, token ni observaciones. Esta prueba **encontró un defecto** que las pruebas en proceso no veían: la auditoría se descartaba en silencio porque el logger no estaba configurado. Quedó corregido (ver ADR-015).

## P5 — adaptador PostgreSQL (rama `dev/p5-postgres`)

Entorno: **PostgreSQL 16.15 real** (binarios del sistema) en un clúster efímero local por ejecución, con psycopg 3.3.6 (binario, libpq 18.0.6) y Python 3.13.16 (`evidence/P5_environment.json`).

Pruebas focalizadas: `python -m unittest tests.persistence.test_postgres -v` — **27 aprobadas**, 0 fallos/errores/omitidas. Evidencia: `evidence/P5_tests.txt`.

- **Contrato del repositorio de P4, sin modificar (15).** Se ejecuta contra PostgreSQL y cubre los mismos casos que los otros dos adaptadores, incluida la concurrencia de 8 escritores.
- **Específicas (7).**
  - Migración idempotente y versionada.
  - UPDATE, DELETE y TRUNCATE sobre eventos rechazados por la base de datos.
  - Detección de 4 manipulaciones (texto de la instantánea, columna de estado, columna de revisión, checksum), tanto en `load` como en `save`.
  - Eventos borrados por el propietario con los triggers desactivados → `CorruptRecord`, `scan` ilegible y recuperación que los cuenta como corruptos.
  - Base de datos inaccesible → `StoreUnavailable` sin DSN, contraseña ni causa encadenada.
  - `run_id` hostiles (inyección SQL, longitud excesiva) rechazados antes de llegar al SQL.
  - 4 procesos concurrentes → 1 ganador.
- **Recuperación (2):** la política completa sobre PostgreSQL, y un **proceso terminado con SIGKILL** con el agente en vuelo, que un proceso nuevo recupera con `INTERRUPTED`, sus eventos previos y 0 resultados.
- **HTTP (1):** 5 duplicados concurrentes con el agente en vuelo → 1 ejecución; después de "reiniciar" con una aplicación y un repositorio nuevos, la repetición es idéntica y sin reejecutar nada.
- **Configuración (2):** el DSN se trata como secreto, `MEDRIXA_STORE_DIR` y `MEDRIXA_PG_DSN` son excluyentes, y la aplicación usa PostgreSQL cuando está configurado.

Regresión justificada (P5 tocó la configuración y la fábrica de la API, y añadió un adaptador al contrato compartido): `python -m unittest discover -s tests -t . -v` da **181 pruebas, 0 fallos, 0 errores, 0 omitidas** (154 previas + 27 nuevas). Evidencia: `evidence/P5_regression.txt`.
- Se repitió 3 veces sin intermitencias.
- También se ejecutó con todas las advertencias como errores, salvo la de terceros ya documentada.
- Con `MEDRIXA_TEST_PG=off` el resultado es `OK (skipped=26)`: las pruebas que necesitan servidor se omiten y no cuentan como aprobadas.
- No quedan procesos ni directorios temporales de PostgreSQL al terminar.

**Verificación de las pruebas:** se introdujeron 4 defectos deliberados (sin comparar la tabla de eventos, sin checksum, sin triggers append-only, sin contrastar columnas con la instantánea). La suite detectó los 4.

**Análisis estático:** ruff con E, F, W, B y S queda limpio. El aviso S608 se resolvió eliminando la concatenación dinámica de SQL, sin silenciarlo.

## P6 — arrendamientos de ejecución (rama `dev/p6-leases`)

Entorno: el mismo de P5 (Python 3.13.16 y PostgreSQL 16.15 real efímero). No hay dependencias nuevas.

Pruebas focalizadas: `python -m unittest tests.persistence.test_leases -v` — **32 aprobadas**, 0 fallos/errores/omitidas. Evidencia: `evidence/P6_tests.txt`.

- **Contrato de arrendamientos (6 × 3 adaptadores, + 1 de archivo y 1 de PostgreSQL).**
  - La adquisición es exclusiva mientras el arrendamiento vive, y solo el titular vivo renueva.
  - Un arrendamiento vencido no revive y puede tomarlo otro dueño (epoch 2).
  - La liberación obsoleta es inofensiva.
  - 8 hilos concurrentes → 1 ganador, y los argumentos inválidos se rechazan.
  - En archivo, un arrendamiento corrupto no otorga vivacidad ni reinicia el epoch. En PostgreSQL, la migración v2 es aditiva.
  - La expiración se observa **esperando en tiempo real**, nunca simulando el reloj.
- **Ejecutor (6).** Cubren:
  - El arrendamiento durante y después de la ejecución.
  - El latido que mantiene viva una ejecución 3 veces más larga que el TTL, que la recuperación omite.
  - La pérdida del arrendamiento: detiene la ejecución sin reintentar, el resultado en vuelo no se acepta.
  - La guarda que impide escribir pasado el plazo local.
  - Un segundo ejecutor que no puede tomar una ejecución viva, y la configuración inválida.
- **Recuperación con arrendamientos (1):** con arrendamiento vivo → `active_elsewhere`; vencido o sin arrendamiento → revisión. El modo P4 sigue intacto.
- **Procesos terminados con SIGKILL (2, archivo y PostgreSQL).** Mientras el arrendamiento del proceso muerto vive, la recuperación lo **protege**; cuando vence, lo **recupera** con `INTERRUPTED` y 0 resultados.
- **Interfaces (3):** la CLI informa `active_elsewhere` solo cuando existe y libera el arrendamiento de su propia ejecución; HTTP lo informa; y `MEDRIXA_LEASE_TTL_SECONDS` respeta sus límites.

Regresión justificada (P6 tocó la recuperación, el gestor persistente, la CLI y la API): `python -m unittest discover -s tests -t . -v` da **213 pruebas, 0 fallos, 0 errores, 0 omitidas** (181 previas + 32 nuevas). Evidencia: `evidence/P6_regression.txt`.
- Se repitió 3 veces sin intermitencias.
- Con las advertencias como errores (salvo la de terceros documentada), pasa.
- Con `MEDRIXA_TEST_PG=off` da `OK (skipped=34)`.
- No quedan procesos de PostgreSQL al terminar.

**Contratos:** los esquemas `RecoveryReport` y `RecoverySummary` y OpenAPI se regeneraron. Las diferencias son **solo inserciones**, verificadas por programa. Las pruebas de P4 y P4b que comparan salidas exactas pasan sin modificarse.

**Verificación de las pruebas:** se introdujeron 6 defectos deliberados (la recuperación ignora arrendamientos, renovar uno vencido, adquirir sin comprobar vivacidad, sin guarda de escritura, sin latido, y PostgreSQL toma un arrendamiento vivo). La suite detectó los 6.

**Análisis estático:** ruff con E, F, W, B y S queda limpio. Los dos S110 se resolvieron: uno con registro constante y otro con supresión explícita justificada.

## P7 — endurecimiento de seguridad (rama `dev/p7-security`)

Entorno: Python 3.13.16 y PostgreSQL 16.15 efímero local (`evidence/P7_environment.json`), con PyJWT 2.15.1, cryptography 50.0.1, psycopg-pool 3.3.3 y mypy 2.4.0.

**Pruebas nuevas: 92.** Evidencia focalizada en `evidence/P7_tests.txt`.

- **Secretos (10, 1 omitida como root).**
  - Lectura y recorte del salto de línea final, y enlaces simbólicos al estilo Kubernetes.
  - Rechazo de archivos escribibles por grupo u otros, vacíos, demasiado grandes, no UTF-8, inexistentes, directorios y enlaces colgantes, sin repetir el valor.
  - Ambigüedad entre `X` y `X_FILE`, nombres restringidos, valores ocultos en `repr` e integración con la configuración.
  - La prueba de archivo ilegible **se ejecutó aparte como usuario no privilegiado** y pasa.
- **OIDC (15)**, contra un **servidor JWKS HTTP real en 127.0.0.1 con claves RSA y EC reales**:
  - Tokens válidos RS256, PS256 y ES256.
  - 22 fallos de claims, entre ellos emisor con prefijo engañoso, emitido en el futuro, vida excesiva, tiempos como `float`, otra organización, roles desconocidos y casos inválidos.
  - Ataques de cabecera y algoritmo: carga y firma alteradas, `alg: none`, **confusión HS256 con la clave pública** (fabricada a mano, porque PyJWT se niega a generarla), algoritmo no permitido, `kid` desconocido o ausente, clave de atacante con el mismo `kid`, discrepancia entre tipo de clave y algoritmo, y `crit`.
  - `alg` de la JWK fijado, sujeto `auth0|…` mapeado y roles anidados.
  - **Rotación de claves:** publicar una nueva, retirar la anterior y caducar la caché.
  - **200 `kid` aleatorios → 1 sola descarga por intervalo.**
  - Caída del IdP con obsolescencia acotada y cierre seguro, y documentos JWKS inválidos (500, redirección, tamaño excesivo, no JSON, vacío).
  - Política de URL y algoritmos, configuración por entorno, y flujo HTTP extremo a extremo con alcance por caso, otra organización y auditoría con organización.
- **Límites (10):** recarga del token bucket con reloj controlado, memoria acotada, `Retry-After` exacto y concurrencia con 16 hilos sin exceder el presupuesto. En HTTP: 429 por IP antes de autenticar, sin llegar a la auditoría; 429 por identidad independiente entre sujetos; host rechazado sin consumir presupuesto; y capacidad agotada → 503 sin crear ejecución.
- **Mínimo privilegio (29):**
  - Las suites de contrato del repositorio (15) y de arrendamientos (6) pasan **sin cambios ejecutadas por el rol de aplicación**.
  - La base de datos rechaza 12 operaciones fuera de la concesión: DELETE, TRUNCATE, UPDATE y DELETE de eventos, desactivar triggers, DROP, ALTER, escribir migraciones, CREATE TABLE, reemplazar la función del trigger y borrar arrendamientos.
  - Un re-`GRANT` no tiene efecto (PostgreSQL solo advierte y no concede nada; se verifica el efecto).
  - Las concesiones coinciden con una especificación independiente, `grant` es idempotente y revoca excesos, y los nombres de rol se validan.
  - El esquema ausente o antiguo falla cerrado, y la CLI `pg_admin` funciona extremo a extremo con HTTP usando el rol de aplicación.
- **Pool (28):** contratos de repositorio y arrendamientos con pool; una transacción fallida deja la conexión limpia; con el pool agotado → `StoreUnavailable` en <3 s; recuperación sin ninguna petición fallida tras `pg_terminate_backend`; el cierre libera las conexiones; un arranque fallido cierra el pool; y la app comparte el pool y lo cierra al apagarse.

**Regresión completa:** `python -m unittest discover -s tests -t . -v` da **305 pruebas, 0 fallos, 0 errores, 1 omitida** (la de root citada arriba). Evidencia: `evidence/P7_regression.txt`.
- Se repitió 3 veces más sin fallos.
- También pasa con las advertencias como errores (salvo la de terceros ya documentada).
- Con `MEDRIXA_TEST_PG=off` el resultado es `OK (skipped=92)`.
- No quedan procesos de PostgreSQL.

**Pruebas intermitentes encontradas y corregidas:** una ejecución de la regresión falló en una prueba de P4b. Su fixture de "firma alterada" sustituía el final de la firma por un sufijo fijo, que coincidía con la firma real cuando esta ya terminaba así (~1/1024), y entonces se aceptaba un token válido. Se corrigió en esa prueba y en la equivalente de OIDC, y se ejecutaron 30 repeticiones limpias. No era un defecto del producto.

**Mutaciones de P7 (19):**
- **OIDC: 8 de 9 detectadas.** Sobrevive la de `crit`, porque PyJWT 2.15 también rechaza extensiones críticas: es redundante, verificado.
- **Límites: 5 de 5.**
- **Mínimo privilegio: 5 de 5**, una de ellas solo después de corregir una prueba circular, que ahora usa una especificación independiente de privilegios.

**Análisis estático:**
- ruff (E, F, W, B, S) limpio. Los avisos de S105 y S310 están justificados y anotados; S608 se evitó sin silenciarlo.
- **mypy 2.4.0 con plugin de pydantic y `check_untyped_defs`: 0 hallazgos en 45 archivos.**
- **pip-audit 2.10.1:** 26 dependencias fijadas, sin vulnerabilidades conocidas (`evidence/P7_dependency_audit.json`).

**Mediciones:** `evidence/P7_latency.json` (ver `docs/PERSISTENCE.md`).
