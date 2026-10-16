# MEDRIXA AI — implementación provisional aislada

Fecha: 2026-10-15, America/Bogota. Código nuevo autorizado por Robinson; no es código histórico recuperado ni una versión clínica.

El diseño histórico permanece como referencia. Los campos de contratos históricos no se recuperaron; la compatibilidad binaria o de API no está acreditada. Esta rama adopta contratos explícitos `medrixa.prototype.v1` para poder compararlos mediante adaptadores cuando aparezcan las fuentes.

## Alcance

Prototipo local con datos sintéticos exclusivamente, sin diagnósticos, tratamientos, conexiones hospitalarias ni llamadas a servicios de IA. FastAPI, PostgreSQL, pgvector, RAG y HL7 FHIR permanecen como objetivos históricos; no se reemplazan por decisiones de producción en este prototipo.

## Continuidad

Leer `CONTINUITY.json`, `TEST_STATUS.md` y `MASTER_SPEC.md`. El repositorio Git se conserva como bundle dentro de cada paquete persistente. No confundir este repositorio provisional con un repositorio histórico recuperado.

## Ejecutar

Entorno verificado: Python 3.12.14, Pydantic 2.13.5, Linux. No se ha probado Windows ni macOS. Desde esta carpeta:

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements-lock.txt
python -m app
python -m app --case data/synthetic_cases/incomplete.json
python -m unittest discover -s tests -t . -v
```

La instalación necesita acceso a un índice de paquetes; el ZIP no incluye dependencias. En el entorno de desarrollo ya estaban disponibles y no se instalaron paquetes. Si Python ya contiene las dependencias, se puede ejecutar la demo directamente.

Resultado del ejemplo completo: dos agentes completados, seis eventos, `awaiting_human_review`, `REVIEW_REQUIRED`. El ejemplo incompleto termina en `MISSING_DATA`. Esto valida coordinación técnica, no capacidades médicas. Código de salida 0 significa simulación ejecutada, **no aprobación clínica**; 2 significa entrada rechazada y 1 fallo de ejecución.

## Recuperar el historial Git

El ZIP incluye el árbol fuente y `MEDRIXA_PROVISIONAL.bundle`. Para recuperar commits y rama:

```bash
git clone MEDRIXA_PROVISIONAL.bundle medrixa_repo
cd medrixa_repo
git log --oneline
```

El árbol entregado está en `medrixa_provisional/`; el repositorio clonado contiene lo mismo y su historial. No hay remoto configurado ni despliegue.

## Contratos y límites

Diez JSON Schema se conservan en `contracts/medrixa.prototype.v1/`; regenerar con `python -m scripts.export_schemas`. `docs/API_CONTRACT.md` delimita la frontera local y la futura API. `docs/REQUIREMENTS_TRACEABILITY.md` relaciona requisitos con pruebas. `docs/RISKS.md` registra límites técnicos y clínicos.

## Persistencia (P4, rama `dev/p4-persistence`)

```bash
python -m app --store ./runs                      # ejecuta y persiste (adaptador local PROVISIONAL)
python -m app --store ./runs --recover            # tras un reinicio: interrumpidas → revisión humana
```

El almacenamiento duradero existe solo mediante el adaptador local provisional de un equipo; PostgreSQL es el destino previsto y **no está implementado**. La recuperación nunca reejecuta agentes. P4 se probó con Python 3.13.16 (P1–P3 con 3.12.14). Ver `docs/PERSISTENCE.md`.

No hay servidor web, motor clínico validado, FHIR implementado ni integración LLM/ML. La próxima tarea exacta está en `CONTINUITY.json` y `docs/NEXT_PACKAGE.md`.
