# Siguiente paquete: decisión sobre P9 (propuesta, no es una fase aprobada)

Base: P8 en `dev/p8-observability` @ 486f815 y el experimento P9 en `exp/p9-execution-offload`, ambos publicados y sin fusionar. Ver `CONTINUITY.json`.

## Estado

El experimento P9 (R30, ADR-031) está **completo y detenido** a la espera de decisión. El informe verificable está en `docs/P9_EXPERIMENT.md`:
- mediciones de la opción C frente a P8 en condiciones equivalentes;
- compatibilidad con agentes y la suite existente;
- condiciones de carrera halladas;
- diseño de la opción B sin implementar;
- análisis de la opción A;
- comparación y recomendación.

## Recomendación (pendiente de Robinson)

1. **No implementar la opción A** (persistencia asíncrona) por ahora. Toca el núcleo estable y el perfil indica que no supera de forma sustancial a C.
2. **Opción C solo endurecida y como mitigación, si se necesita antes de B.** Requiere:
   - liberar la plaza de capacidad al terminar el hilo;
   - una regla de contrato para agentes (sin objetos asyncio compartidos);
   - documentar la semántica de cancelación;
   - un plazo de apagado y una métrica de hilos;
   - adaptar las 4 pruebas ligadas al bucle.
3. **Reducir los viajes a la base por ejecución.** Hoy son 9 transacciones, 36 sentencias y 9 comprobaciones del pool. Es el techo medido; toca la persistencia y requiere autorización.
4. **Opción B** (202 + trabajador, contrato versionado) como arquitectura objetivo para trabajos prolongados y escalado en varios procesos. Primero hay que decidir el contrato (`Prefer: respond-async` o `/v2`), la cancelación y el limitador y la capacidad compartidos.

## Decisiones necesarias

Las de `docs/P9_EXPERIMENT.md` §10. Hasta que se tomen no se implementa la arquitectura definitiva de P9.

## Trabajo adicional independiente (sin cambios)

Adaptador OpenTelemetry para el puerto de métricas, limitador de tasa compartido (PostgreSQL), contenedor reproducible sin publicar y diseño de contratos FHIR sin afirmar conformidad.

## Condiciones

Sin despliegue, datos reales, servicios de pago, selección de proveedores ni fusiones sin autorización. Un cambio de contrato HTTP (opción B) se versiona y se documenta antes de implementarse.
