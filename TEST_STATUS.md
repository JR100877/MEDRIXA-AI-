# Estado de pruebas

P1 — comando: `python -m unittest tests.core.test_state -v`

Entorno: Python 3.12.14, Pydantic 2.13.5. Resultado observado: 17 pruebas, 0 errores, 0 fallos, 0 omitidas; 0.007 s. Verifica validación de contratos, rechazo de datos marcados como reales, inmutabilidad, serialización, correlación, atomicidad, concurrencia y aislamiento.

No es validación clínica, evaluación de seguridad productiva ni prueba de conformidad FHIR. No se ejecutó pytest porque no está instalado.

P2 — `python -m unittest tests.test_agents tests.core.test_engine -v`: 22 pruebas aprobadas, 0 errores/fallos/omitidas; 0.049 s. Evidencia literal: `evidence/P2_tests.txt`. P1 no se repitió porque este paquete no cambió sus fuentes.
