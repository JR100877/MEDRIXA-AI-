# P6 — siguiente paquete exacto (propuesta de planificación, no fase aprobada)

Base: P5 en la rama `dev/p5-postgres`, publicada y sin fusionar. Ver `CONTINUITY.json`.

## Objetivo

Que la recuperación pueda ejecutarse **con ejecutores vivos en otros procesos** sin interrumpirlos. Hoy la recuperación debe correr sin ejecutores activos, y `RUNS_ACTIVE` solo protege al propio proceso.

## Diseño propuesto

1. **Puerto `RunLeases`, separado de `StateRepository`:** `acquire(run_id, owner, ttl)`, `renew(run_id, owner, epoch)` y `release(run_id, owner, epoch)`. Los adaptadores serían en memoria, archivo y PostgreSQL (tabla `medrixa_run_leases` con `epoch` monótono).
2. **Token de cercado (fencing):** cada transición persistida lleva el `epoch` del arrendamiento vigente, y un ejecutor con un arrendamiento vencido no puede escribir. Hay que decidir si va dentro del CAS del repositorio (cambio aditivo del puerto) o como comprobación previa en `PersistentStateManager`.
3. **Ejecutor:** latido periódico mientras un agente está en vuelo. Si la renovación falla, la ejecución se detiene y queda para recuperación; nunca se reintenta automáticamente.
4. **Recuperación:** solo toca ejecuciones `running` sin arrendamiento o con el arrendamiento vencido. Las que tienen arrendamiento vivo se informan como `active_elsewhere`.
5. **Relojes:** las expiraciones se calculan con la hora de la base de datos en PostgreSQL. En archivos, el arrendamiento queda limitado a un solo equipo, y se documenta.

## Pruebas

Las pruebas serán de contrato, iguales para los tres adaptadores:
- Dos procesos compiten por un arrendamiento.
- Un arrendamiento vencido se puede tomar.
- Un ejecutor cercado no puede escribir.
- La recuperación respeta los arrendamientos vivos.
- Un proceso terminado con SIGKILL se recupera solo tras vencer su arrendamiento.

## Decisiones humanas pendientes

TTL y periodo de latido por defecto, y si la aplicación HTTP debe ejecutar la recuperación automáticamente al arrancar.

## Condiciones

Sin cambios incompatibles en contratos publicados. Fusionar ramas, usar infraestructura gestionada o desplegar requiere autorización.
