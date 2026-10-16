# Decisiones de ingeniería

## ADR-001 — Área aislada y contratos nuevos

No se recuperó fuente histórica. Se conserva el diseño y se crea la rama `provisional/2026-10-15`, sin asignar versión histórica. Los contratos `medrixa.prototype.v1` son nuevos y deben compararse antes de cualquier migración futura. Tener ambos archivos `state.py` (datos) y `state_manager.py` (transiciones) es una separación provisional explícita, no una reconciliación histórica demostrada.

## ADR-002 — Python y Pydantic disponibles

Python 3.12.14 y Pydantic 2.13.5 están disponibles. Se fija esta dependencia para reproducibilidad. No se instalaron paquetes. Se usa `unittest` de la biblioteca estándar porque pytest no está disponible; las pruebas escritas como TestCase también podrán ejecutarse con pytest cuando se habilite. La elección no sustituye FastAPI/PostgreSQL/pgvector previstos.

## ADR-003 — Seguridad por restricción de alcance

Solo datos sintéticos, contratos sin campos de identificación directa, reglas técnicas sin inferencia clínica y revisión humana terminal obligatoria. La marca `synthetic=True` no detecta PHI ni prueba anonimización. No introducir datos reales, incluso marcados como sintéticos. Identificadores y códigos también deben ser sintéticos. La auditoría contiene identificadores opacos y códigos, no valores de observaciones.

## ADR-004 — Estado local y atómico

RLock para modificaciones en un proceso, revisión esperada para evitar actualizaciones perdidas y validación completa antes de publicar la nueva instantánea. No es una transacción distribuida ni almacenamiento duradero. El repositorio sí se conserva mediante Git bundle y paquete persistente.

## Fuentes técnicas consultadas

- Pydantic, modelos y validación estricta: https://github.com/pydantic/pydantic/blob/main/docs/concepts/models.md y https://github.com/pydantic/pydantic/blob/main/docs/concepts/strict_mode.md (consulta 2026-10-15). Justifican revalidar entradas y evitar considerar model_copy/model_construct una validación.
- Python asyncio: https://docs.python.org/3/library/asyncio-task.html (consulta 2026-10-15). Para el siguiente paquete, los tiempos de espera serán cooperativos; no se prometerá detener código bloqueante o no confiable.
