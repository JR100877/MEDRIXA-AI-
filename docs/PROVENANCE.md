# Procedencia y preservación

Se leyeron los dos informes actuales:

- MEDRIXA_ARQUITECTURA_RECUPERADA_2026_10_15.md — libfile_349532f447588191b8780c61bd529c7c. Fuente secundaria; corrige el pendiente histórico: Orchestrator conceptual después de State/Planner/Router, pruebas aún no acreditadas.
- MEDRIXA_AUDITORIA_CONTINUIDAD_2026_10_15.md — libfile_ef76192cfddc8191a044d8bb96416867. Fuente secundaria anterior, sin código recuperado.

La búsqueda actual por título MEDRIXA devolvió estos dos informes y ningún paquete fuente identificado. El directorio de trabajo no contenía código. No se repitieron las búsquedas completas de Drive/GitHub realizadas por la auditoría previa. No se afirma inexistencia universal de originales.

Referencias preservadas: árbol de 07/09 con `state_manager.py`, directorios de agentes; variante de 11/09 con `state.py` y agentes planos. Esta línea nueva usa `state.py` como modelo y `state_manager.py` como servicio, y directorios de agentes. Es una decisión provisional documentada, no un renombrado histórico ni una fusión acreditada.

Se conservan Python/Pydantic, CORE, agentes, Planner puro, Router sin ejecución, supervisión humana; FastAPI, PostgreSQL/pgvector, RAG, HL7 FHIR siguen pendientes. Ningún archivo histórico fue editado.
