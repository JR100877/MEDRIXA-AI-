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
