# MEDRIXA — especificación provisional de implementación

Fuente rectora: autorización de dirección autónoma de ingeniería de Robinson del 15/10/2026. Estado: alcance autorizado; decisiones de implementación provisionales, no arquitectura histórica certificada.

## Requisitos verificables

- R01: contratos Pydantic estrictos, inmutables, sin campos extra; procedencia sintética obligatoria.
- R02: State explícito; StateManager controla transiciones, revisión optimista, aislamiento entre casos y resultados.
- R03: Planner puro: propone pasos; Router solo resuelve agentes, sin ejecutarlos.
- R04: Orchestrator ejecuta un flujo acotado; fallos, ruta desconocida, tiempo agotado o resultados inválidos terminan en revisión humana.
- R05: HistoryAgent y ClinicalAgent son simuladores técnicos sin inferencia clínica ni tratamiento; todo flujo termina en revisión humana.
- R06: mensajes/resultados vinculados por caso, ejecución, solicitud y agente; rechazo de reutilización indebida.
- R07: auditoría estructurada de transiciones, sin texto de casos ni excepciones en los eventos; pruebas de errores y aislamiento.
- R08: desarrollo reproducible con evidencia, Git y continuidad persistida; código nuevo separado de antecedentes.
- R09: independencia de NEXARIS; integración futura solo por contratos y permisos explícitos.
- R10: no datos reales, uso clínico, producción, costos ni secretos. La marca sintética es un control de desarrollo, no un detector de información personal.

## Requisitos P4 (autorización de Robinson, 15/10/2026, 21:18)

- R11: puerto `StateRepository` con persistencia controlada por revisión; pruebas de concurrencia, atomicidad, corrupción, duplicación y aislamiento entre casos. Adaptador local identificado como provisional; PostgreSQL sigue como destino, sin declararse implementado.
- R12: recuperación segura de ejecuciones interrumpidas, sin reintento automático de agentes y con revisión humana.
- R13: conservar contratos y pruebas existentes; rama de desarrollo separada; sin fusión a la rama principal sin nueva autorización.

## Requisitos P4b (autorización de desarrollo autónomo supervisado de Robinson, 15/10/2026, 21:39)

- R14: API REST con contratos HTTP versionados, validación estricta, serialización controlada, respuestas consistentes y OpenAPI.
- R15: autenticación desacoplada con identidades verificables; autorización por caso, mínimo privilegio, denegación por defecto y protección contra accesos cruzados.
- R16: claves de idempotencia, prevención de duplicados, gestión de conflictos y consistencia ante reintentos, con pruebas de concurrencia.
- R17: mapeo explícito de excepciones, errores estructurados sin información interna y con identificador de solicitud.
- R18: configuración segura por defecto, límites de entrada, separación de entornos y auditoría sin datos clínicos innecesarios.

Sigue prohibido: fusionar a la rama predeterminada, reescribir commits publicados, hacer force push, usar datos reales, desplegar en producción, tomar decisiones clínicas autónomas, cambiar la visibilidad del repositorio y declarar validación clínica.

## Requisitos P5 (misma autorización; identificador propuesto, no fase aprobada)

- R19: adaptador PostgreSQL compatible con `StateRepository`, que pase sin cambios la suite de contrato. Se prueba solo contra PostgreSQL local efímero; ninguna base gestionada, compartida ni con datos reales.

## Requisitos P6 (misma autorización; identificador propuesto)

- R20: arrendamiento de ejecuciones, coordinación y recuperación segura entre procesos, sin reintentar agentes y sin cambiar `StateRepository`.

## Requisitos P7 (instrucción de desarrollo autónomo supervisado de Robinson, 16/10/2026)

- R21: secretos por interfaces compatibles con proveedores externos, sin credenciales en el código.
- R22: identidad OIDC por un puerto desacoplado, con validación robusta de tokens (issuer, audience, expiración, claves públicas, rotación), sin proveedor comercial definitivo ni criptografía propia.
- R23: autorización por rol, organización y caso, con pruebas de aislamiento. La organización es única hasta decidir el modelo de multi-tenancy.
- R24: limitación de tasa configurable, límites de recursos y protección frente a entradas maliciosas.
- R25: PostgreSQL de mínimo privilegio, migraciones controladas y pool justificado por medición.
- R26: TLS y cifrado en reposo documentados como requisitos de despliegue, con responsabilidades y límites.

## Requisitos P8 (misma autorización; identificador propuesto)

- R27: observabilidad independiente del proveedor: métricas sin datos clínicos ni identificadores, con acceso restringido.
- R28: sonda de preparación sin revelar detalles internos.
- R29: correlación de registros por identificador de solicitud y medición reproducible de rendimiento.

## Experimento P9 (autorización de Robinson, 16/10/2026, 13:03)

- R30: investigar y validar de forma **experimental** cómo eliminar el bloqueo del bucle de eventos causado por la E/S síncrona de PostgreSQL, preservando la compatibilidad, la seguridad y la estabilidad de P8. Incluye: rama experimental independiente derivada de P8, evaluación de la opción C con atención a los agentes, mediciones comparables con la línea base y documentación de la opción B sin implementarla ni cambiar el contrato HTTP. Prohibido modificar el núcleo estable, la seguridad, el modelo de datos o los contratos públicos sin autorización; desplegar; fusionar; usar servicios de pago; elegir proveedores externos; hacer cambios irreversibles e implementar la arquitectura definitiva de P9 sin aprobación.

## Exclusiones del incremento

Autenticación productiva, cifrado persistente, aislamiento de código no confiable, validación clínica, regulación, FHIR conforme, modelos LLM/ML y bases de conocimiento médicas. No se declararán implementados.
