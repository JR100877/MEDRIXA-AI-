# P9 — siguiente paquete (propuesta de planificación, no fase aprobada)

Base: P8 en la rama `dev/p8-observability`, publicada y sin fusionar. Ver `CONTINUITY.json`.

## Problema medido

`evidence/P8_load.json` y ADR-030: con PostgreSQL, las llamadas síncronas de psycopg dentro de la ejecución asíncrona bloquean el bucle de eventos. El rendimiento se queda en ~45–48 req/s entre 1 y 8 clientes, y la latencia crece de forma lineal.

## Opciones (evaluar antes de implementar)

1. **Puerto de persistencia asíncrono** (`AsyncStateRepository`, psycopg `AsyncConnection` y `AsyncConnectionPool`), con el `StateManager` y el Orchestrator usando `await` en las transiciones. Es el cambio más profundo: el CORE pasa a ser asíncrono en la persistencia, y la suite de contrato necesitaría su versión asíncrona.
2. **Ejecutor fuera del ciclo HTTP:** `POST /v1/runs` crea la ejecución y responde 202. Un trabajador, en un hilo o proceso propio, la ejecuta bajo arrendamiento, y el cliente consulta `GET /v1/runs/{id}`. Preserva la idempotencia (`run_id` determinista), el fencing (arrendamientos y CAS) y el no reintento. Cambia el contrato HTTP de 201 síncrono a 202 asíncrono, así que necesita versionado (`medrixa.http.v2`) o una cabecera opcional.
3. **Mitigación mínima:** ejecutar cada ejecución completa en un hilo de trabajo (`to_thread` con su propio bucle). Mejora la concurrencia sin cambiar contratos, pero con el costo de hilos por ejecución, acotado por `MEDRIXA_MAX_ACTIVE_RUNS`.

**Recomendación técnica:** medir primero la opción 3 (cambio pequeño y reversible) con `scripts/load_test.py`. Si basta, adoptarla, y diseñar la 2 para trabajos prolongados reales. La 1 solo si la 2 no es suficiente.

## Trabajo adicional independiente

Adaptador OpenTelemetry para el puerto de métricas (SDK como dependencia opcional), limitador de tasa compartido (PostgreSQL), contenedor reproducible sin publicar y diseño de contratos FHIR sin afirmar conformidad.

## Condiciones

Sin despliegue, datos reales, servicios de pago ni fusiones sin autorización. Un cambio de contrato HTTP (opción 2) se versiona y se documenta antes de implementarse.
