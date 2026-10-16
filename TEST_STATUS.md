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
