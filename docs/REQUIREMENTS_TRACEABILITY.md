# Trazabilidad de requisitos

| Requisito | Implementación | Evidencia |
|---|---|---|
| R01 contratos estrictos y sintéticos | models/contracts.py; api/contracts.py | ContractTests; BoundaryTests, marcadores numéricos/ausentes, JSON inválido/duplicado/grande |
| R02 transiciones y revisión | core/state.py; core/state_manager.py | StateManagerTests: atomicidad, correlación, un ganador concurrente, terminal sin reinicio |
| R03 separación Planner/Router | core/planner.py; core/router.py | PlannerRouterTests: pureza, registro copiado, rutas y agentes inválidos |
| R04 ejecución acotada | core/orchestrator.py | OrchestratorTests; HardeningTests: fallos, timeout, cancelación, orden, repetición y límite |
| R05 simulación y revisión | agents/history/agent.py; agents/clinical/agent.py | AgentTests; integración completa e incompleta; demo_summary.json |
| R06 correlación | AgentContext; StateManager.accept; State | Pruebas de caso/run/mensaje/agente equivocados, duplicados y ocho casos concurrentes |
| R07 observabilidad sin payload | AuditEvent; observability/summary.py | Pruebas de errores sanitizados, secuencia manipulada y resumen sin identificador |
| R08 continuidad | Git bundle; siete documentos; manifest | Commit por paquete, salida de pruebas y manifest de fuentes |
| R09 independencia | imports internos app; sin dependencia de otro proyecto | Árbol e inventario; integración futura documentada |
| R10 límites autorizados | CLI local, fixtures, ausencia de clientes de red | Inspección de código y pruebas sintéticas; no es control DLP ni validación clínica |

Se distingue evidencia automatizada de inspección: independencia y ausencia de servicios externos se constatan en las fuentes, no mediante una certificación de seguridad.
