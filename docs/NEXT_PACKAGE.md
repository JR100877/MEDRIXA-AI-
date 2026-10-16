# P8 — siguiente paquete (propuesta de planificación, no fase aprobada)

Base: P7 en la rama `dev/p7-security`, publicada y sin fusionar. Ver `CONTINUITY.json`.

## Bloqueos vigentes (decisión de Robinson)

Proveedor de identidad, gestor de secretos y custodia de claves, cifrado en reposo, modelo de multi-tenancy y valores operativos definitivos. Las opciones y la recomendación están en `docs/SECURITY.md` §4. Ninguno impide P8.

## Objetivo de P8: observabilidad independiente del proveedor

1. **Métricas por un puerto**, con un adaptador en memoria para pruebas y otro opcional para OpenTelemetry (`opentelemetry-api` ya está en el cierre de dependencias por FastAPI). Se medirán latencia por ruta, códigos de estado, rechazos por límite, capacidad en uso, pool (uso y espera), arrendamientos (renovaciones y pérdidas) y recuperación.
2. **Trazas** con un `request_id` propagado a la auditoría, a los registros de persistencia y a los arrendamientos, sin incluir carga clínica.
3. **Endpoint de preparación** (`/v1/ready`) que compruebe almacén, esquema e IdP (JWKS en caché), separado de `/v1/health`, sin revelar detalles.
4. **Pruebas de carga reproducibles** locales para dimensionar `MEDRIXA_MAX_ACTIVE_RUNS`, los límites de tasa y el pool con datos reales del equipo.

## Trabajo técnico independiente posterior (P9, propuesta)

Ejecución en segundo plano fuera del ciclo HTTP (preservando idempotencia, fencing y no reintento de agentes), un limitador de tasa compartido (adaptador PostgreSQL), un contenedor reproducible sin publicar y diseño de contratos de interoperabilidad (FHIR) sin afirmar conformidad.

## Condiciones

Sin despliegue, datos reales, servicios de pago ni fusiones sin autorización.
