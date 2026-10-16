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
