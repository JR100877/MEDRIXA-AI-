# Frontera de intercambio provisional

Estado: implementada como función local y CLI. **No hay servidor HTTP ni endpoints implementados.** FastAPI permanece como tecnología histórica prevista.

## Entrada local

`app.api.contracts.parse_case(payload: bytes) -> ClinicalCase`.

- UTF-8, JSON objeto, máximo 1 MiB, sin claves duplicadas, constantes NaN/Infinity, coerciones de booleanos ni campos extra.
- `synthetic: true`, `source: "synthetic_fixture"` e `history_available` obligatorios.
- `observations` opcional, máximo 100; código único por caso y valor finito. Los códigos y unidades de la demo son ficticios, no terminologías clínicas.
- Identificadores opacos, máximo 80 caracteres. No incluir información personal en ellos.
- Error externo uniforme `INVALID_INPUT`, sin reflejar la entrada. La CLI rechaza el archivo y retorna 2.
- El límite de bytes se aplica en la frontera local; instanciar modelos directamente no sustituye esta frontera.

## Contratos internos

`AgentContext` contiene ClinicalCase, AgentMessage y resultados previos. El mensaje identifica caso, ejecución, solicitud, agente y paso. AgentResult debe corresponder exactamente al mensaje pendiente. Los IDs duplicados y resultados cruzados se rechazan. Los agentes no reciben StateManager.

Cada esquema incluye `schema_version: medrixa.prototype.v1`. Son contratos nuevos. No representan equivalencia con modelos de septiembre ni conformidad FHIR.

## Salida local

`RunSummary`: estado, motivo de detención y contadores de mensajes/resultados/eventos. No incluye identificadores ni observaciones. El estado detallado queda en memoria. Todos los resultados requieren revisión humana; el prototipo no dispone de función de aprobación clínica.

## Persistencia local (P4)

- `python -m app --store DIR [--case FILE]`: ejecuta y persiste en el adaptador local **provisional**. Si el directorio no puede abrirse, imprime `{"error":"STORE_UNAVAILABLE"}` y retorna 2. Si el almacén falla durante la ejecución, imprime lo mismo y retorna 1; el registro conserva la última revisión persistida. Esta segunda ruta está cubierta en pruebas del gestor, no en una prueba de CLI.
- `python -m app --store DIR --recover`: imprime `RecoverySummary` (solo contadores, sin identificadores) y retorna 0, o 1 si hay registros corruptos. `--recover` sin `--store`, o junto con `--case`, retorna 2 con `INVALID_INPUT`.

## Adaptador HTTP futuro (diseño, no implementación)

P4b definirá el adaptador FastAPI sobre el servicio y el repositorio. Antes de exponerlo: autenticación, autorización por caso, límites de petición, política de errores y reintentos, idempotencia, persistencia, exclusión de cuerpos sensibles de logs y pruebas de acceso. No se publica una URL ni se generan credenciales ficticias. La interfaz futura será independiente de NEXARIS.
