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
| R11 persistencia con control de revisiones | persistence/repository.py; memory.py; local_file.py; manager.py | RepositoryContract ×2 adaptadores: CAS, una transición, reescritura, caso cruzado, duplicado, concurrencia (hilos y procesos), atomicidad ante fallo de escritura; LocalFileTests de corrupción |
| R12 recuperación segura | persistence/recovery.py; CLI `--recover` | RecoveryPolicyTests; ProcessRestartTests con SIGKILL real; 0 agentes reejecutados; resultado tardío rechazado |
| R13 contratos conservados | gancho `_publish`; `StopCode` aditivo | 52 pruebas previas sin modificar; ContractCompatibilityTests |
| R14 API REST versionada y documentada | web/contracts.py; web/app.py; contracts/http/openapi.v1.json | HttpContractTests (formas, límites, OpenAPI publicado = generado) |
| R15 autenticación y autorización por caso | web/auth.py; web/policy.py; web/service.py | TokenTests; PolicyTests; AuthorizationTests; mutaciones M1–M3, M7 |
| R16 idempotencia | web/service.py (`derive_run_id`); manager `run_id` | IdempotencySyncTests; IdempotencyConcurrencyTests; mutaciones M4–M5 |
| R17 errores seguros y trazables | web/errors.py; manejadores en web/app.py | Aserciones de sobre y ausencia de eco en todas las suites web; mutación M8 |
| R18 seguridad por defecto y auditoría | web/settings.py; web/audit.py; `serve` en 127.0.0.1 | SettingsTests; AdverseTests; prueba con servidor real (evidencia en TEST_STATUS) |
| R19 adaptador PostgreSQL compatible con StateRepository | persistence/postgres.py; `MEDRIXA_PG_DSN` | PostgresRepositoryContractTests (suite P4 sin cambios); PostgresSpecificTests; PostgresProcessRestartTests; PostgresHttpTests; mutaciones P5 M1–M4 |
| R20 arrendamiento y recuperación entre procesos | persistence/leases.py; leasing.py; recovery `leases`; postgres `PostgresRunLeases` | LeaseContract ×3 adaptadores; LeasedExecutionTests; LeaseAwareRecoveryTests; LeasedProcessRestartTests (SIGKILL, archivo y PostgreSQL); mutaciones P6 M1–M6 |
| R21 secretos | security/secrets.py; settings | SecretFileTests; ProviderTests; SettingsIntegrationTests (+ prueba como no root) |
| R22 OIDC | web/oidc.py; settings `MEDRIXA_AUTH_MODE` | ValidationTests; JwksTests (rotación, `kid` aleatorios, caída del IdP); HttpIntegrationTests; mutaciones P7.2 |
| R23 organización y caso | web/oidc.py (organización); web/policy.py | HttpIntegrationTests (otra organización → 401, otro caso → 404); AuthorizationTests |
| R24 límites | security/ratelimit.py; web/app.py; web/service.py | TokenBucketTests; HttpRateLimitTests; CapacityTests; mutaciones P7.3 |
| R25 PostgreSQL de mínimo privilegio y pool | persistence/pg_admin.py; postgres.py | AppRole*ContractTests; PrivilegeBoundaryTests; AdminCliTests; Pooled*ContractTests; PoolBehaviourTests; evidence/P7_latency.json |
| R26 requisitos de despliegue | docs/SECURITY.md | Inspección documental (no automatizable) |
| R27 métricas | observability/metrics.py; web/app.py; policy `metrics:read` | RegistryTests; HttpMetricsTests (sin identificadores; solo operador); mutaciones P8 |
| R28 preparación | `/v1/ready`; ganchos `ping()` y `ready()` | ReadinessTests (auditoría, almacén, directorio, JWKS, PostgreSQL eliminado) |
| R29 correlación y carga | observability/context.py; scripts/load_test.py | OperationalLoggingTests (hilo de autenticación); evidence/P8_load.json |

Se distingue evidencia automatizada de inspección: independencia y ausencia de servicios externos se constatan en las fuentes, no mediante una certificación de seguridad.
