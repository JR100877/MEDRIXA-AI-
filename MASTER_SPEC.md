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

## Exclusiones del incremento

Autenticación productiva, cifrado persistente, aislamiento de código no confiable, validación clínica, regulación, FHIR conforme, modelos LLM/ML y bases de conocimiento médicas. No se declararán implementados.
