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

## Exclusiones del incremento

Autenticación productiva, cifrado persistente, aislamiento de código no confiable, validación clínica, regulación, FHIR conforme, modelos LLM/ML y bases de conocimiento médicas. No se declararán implementados.
