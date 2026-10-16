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
