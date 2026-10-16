# Estado de pruebas

P1 — comando: `python -m unittest tests.core.test_state -v`

Entorno: Python 3.12.14, Pydantic 2.13.5. Resultado observado: 17 pruebas, 0 errores, 0 fallos, 0 omitidas; 0.007 s. Verifica validación de contratos, rechazo de datos marcados como reales, inmutabilidad, serialización, correlación, atomicidad, concurrencia y aislamiento.

No es validación clínica, evaluación de seguridad productiva ni prueba de conformidad FHIR. No se ejecutó pytest porque no está instalado.
