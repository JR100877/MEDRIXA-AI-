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
