# Arquitectura de la línea provisional

## Base preservada

La arquitectura documental histórica se mantiene: API FastAPI → CORE/Orchestrator → agentes especializados; inteligencia híbrida LLM/ML/reglas, RAG/pgvector y PostgreSQL; auditoría y supervisión humana. Estos nombres no prueban implementación. Consultar `docs/PROVENANCE.md`.

## Contratos nuevos

- `ClinicalCase`: caso sintético, identificador opaco, disponibilidad de historia, observaciones sintéticas finitas con procedencia.
- `AgentMessage`: solicitud con caso, ejecución, mensaje, agente y paso.
- `AgentResult`: respuesta correlacionada, estado técnico y hallazgos de completitud; revisión humana obligatoria.
- `AgentContext`: entrada inmutable con caso, solicitud y resultados previos de la misma ejecución.
- `State`: instantánea inmutable y validada. `StateManager`: dueño local de las transiciones; bloqueo RLock y control optimista por revisión.

Las colecciones son tuplas, los objetos anidados son contratos congelados. Los límites reciben modelos revalidados; no se usa `model_copy(update=...)` como validación. La congelación de Python no constituye una barrera contra código malicioso dentro del mismo proceso.

Estado inicial `created` → `running` → `awaiting_human_review`. No existe transición a una decisión clínica aprobada. El gestor mantiene mensajes, resultados y eventos en memoria. No se ofrece todavía recuperación de ejecuciones tras caída ni persistencia de historias clínicas.

## Separación de capas

| Área | Estado en P1 |
|---|---|
| CORE/contratos | Implementados y probados |
| Agent Engine | Siguiente paquete |
| Knowledge Engine | Futuras reglas clínicas; solo simulación de completitud prevista |
| Clinical Data | Contratos sintéticos; FHIR pendiente |
| AI Engine | Puertos futuros, sin proveedor conectado |
| Security | Validación y aislamiento lógico local; autenticación/cifrado productivo pendientes |
| Observability | Eventos inmutables en State; métricas pendientes |
| Interface | CLI prevista; frontend y API FastAPI pendientes |

MEDRIXA sigue independiente; ningún módulo importa NEXARIS ni necesita otro proyecto.

## Implementación P2

`Planner.create_plan(state)` devuelve Plan validado sin efectos; `Router.resolve(step)` devuelve un agente del registro sin ejecutarlo. Orchestrator crea AgentContext, espera al agente, valida/correlaciona AgentResult y delega transiciones al StateManager. HistoryAgent y ClinicalAgent comprueban presencia de datos; nunca interpretan valores ni generan diagnósticos. Todos los recorridos válidos terminan en revisión humana.

## Estado actual tras P3

| Área | Implementado | Pendiente |
|---|---|---|
| CORE | State, StateManager, contratos y transiciones | Persistencia y recuperación tras caída |
| Agent Engine | Planner, Router, Orchestrator, dos simuladores | Aislamiento de agentes no confiables; más agentes |
| Knowledge Engine | Dos comprobaciones técnicas de presencia dentro de agentes | Motor de conocimiento clínico, fuentes y validación |
| Clinical Data | Modelos sintéticos y frontera JSON validada | Adaptador FHIR y perfiles |
| AI Engine | Sin proveedor conectado | Puertos LLM/ML/RAG y evaluación |
| Security | Validación, correlación, revisión humana y errores sin payload | AuthN/AuthZ, cifrado y controles productivos |
| Observability | Eventos y resumen de contadores | Exportación a backend y retención segura |
| Interface | CLI y ocho esquemas JSON | HTTP FastAPI y frontend |

Los esquemas no representan endpoints activos. Esta línea no incorpora IoT. El repositorio y sus documentos se conservan; el estado de simulaciones sigue en memoria.

## Persistencia P4

```
Orchestrator ──► PersistentStateManager ──save(CAS)──► StateRepository (puerto)
                   (persistir, luego publicar)              ├─ InMemoryStateRepository       [provisional, pruebas]
                                                            ├─ ProvisionalLocalFileRepository [provisional, un equipo]
recover_interrupted_runs ──scan/load/save──────────────────►└─ PostgreSQL                      [destino previsto, NO implementado]
```

El Orchestrator no cambió: usa el gestor persistente igual que el gestor en memoria. Un `RevisionConflict` hereda de `StateConflict` y se relanza sin sobrescribir. Las ejecuciones sobreviven al reinicio del proceso con el adaptador local. Lo interrumpido termina en revisión humana (`INTERRUPTED`) sin reejecutar agentes. Detalle y diseño PostgreSQL en `docs/PERSISTENCE.md`.

| Área | Tras P4 | Pendiente |
|---|---|---|
| CORE | + puerto de repositorio, CAS, recuperación | Adaptador PostgreSQL; arrendamiento de ejecuciones |
| Interface | CLI `--store`/`--recover`; 10 esquemas | HTTP FastAPI (P4b) y frontend |
| Security | + historial solo extensible, checksum de corrupción, permisos de archivo | Firma, cifrado en reposo, AuthN/AuthZ |

## Adaptador HTTP P4b

```
HTTP ─► FastAPI (app/web/app.py) ─► RunService (app/web/service.py) ─► PersistentStateManager ─► StateRepository
          │ host allow-list, límites,      │ Authenticator (puerto) ─ HmacTokenAuthenticator [provisional]
          │ errores uniformes, request id  │ policy.require (roles × caso, denegar por defecto)
          │ cabeceras de seguridad         │ AuditSink (puerto) ─ Logging/Memory  [falla cerrada]
          └ OpenAPI (solo development)     └ idempotencia: run_id = H(sujeto, clave) + create atómico
```

FastAPI solo traduce. Las reglas viven en `RunService`, que puede reutilizarse en otro transporte. El CORE (Orchestrator, Planner, Router y agentes) no cambió. Detalle en `docs/HTTP_API.md`.

| Área | Tras P4b | Pendiente |
|---|---|---|
| Interface | API HTTP local v1 con 5 endpoints, OpenAPI y CLI de servidor local | Frontend; ejecución en segundo plano |
| Security | AuthN por puerto, AuthZ por rol y caso, auditoría de accesos, límites, cabeceras y configuración segura | OIDC/IdP, rotación y revocación, limitación de tasa, TLS (P7) |
| Observability | Auditoría de accesos en líneas JSON con `request_id` | Métricas y trazas (P8) |
