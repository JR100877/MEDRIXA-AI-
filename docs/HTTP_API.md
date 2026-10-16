# API HTTP local `medrixa.http.v1` (P4b)

Estado: **adaptador FastAPI local implementado y probado**, solo con datos sintéticos. No hay despliegue ni perfil productivo. La autenticación productiva (OIDC o un proveedor de identidad) **no está implementada**. Contrato publicado: `contracts/http/openapi.v1.json`, que se regenera con `python -m scripts.export_openapi`.

## Endpoints

| Método y ruta | Permiso | Respuesta |
|---|---|---|
| `GET /v1/health` | ninguno | `HealthView` (sin versión de software ni entorno) |
| `POST /v1/runs` | `runs:create` + caso en alcance | 201 `RunView`; cabeceras `Location` e `Idempotent-Replayed` |
| `GET /v1/runs/{run_id}` | `runs:read` + caso en alcance | 200 `RunView` |
| `GET /v1/runs/{run_id}/events` | `runs:read` + caso en alcance | 200 `RunEventsView` (secuencia, tipo, hora y motivo) |
| `POST /v1/admin/recovery` | `runs:recover` | 200 `RecoveryView` (solo contadores) |

Las respuestas nunca incluyen observaciones ni hallazgos. Todo resultado lleva `human_review_required: true`, y no existe un endpoint ni un permiso de aprobación clínica.

## Autenticación

- Se hace a través del puerto `Authenticator` (`app/web/auth.py`), y la API depende solo de `Principal`.
- **El adaptador provisional `HmacTokenAuthenticator`** usa tokens `mdx1.<claims>.<HMAC-SHA256>`. Verifica la firma en tiempo constante, el emisor, la audiencia, la expiración, la vida máxima (8 h; 1 h por defecto), la tolerancia de reloj (30 s), los roles conocidos y los claims estrictos.
- **Respuesta uniforme a los fallos:** cualquier fallo da 401 `UNAUTHENTICATED` con `WWW-Authenticate: Bearer`, sin indicar qué comprobación falló.
- **Límites:** es un secreto compartido. No hay revocación, rotación de claves ni proveedor de identidad. Es solo para desarrollo y pruebas.
- **Tokens locales:** se emiten con `python -m app.web issue-token --sub ... --role ... --case ...`, usando `MEDRIXA_AUTH_SECRET`.

## Autorización (`app/web/policy.py`)

- **Denegación por defecto.** Un rol desconocido invalida el token.

| Rol | Permisos | Alcance |
|---|---|---|
| `case_runner` | `runs:create`, `runs:read` | casos del token |
| `case_reviewer` | `runs:read` | casos del token |
| `operator` | `runs:recover` | ninguno: no lee ejecuciones |

- **Accesos cruzados:** leer una ejecución de un caso fuera de alcance responde **404 idéntico** al de una ejecución inexistente, para no revelar su existencia.
- **Permiso ausente:** sin el permiso de lectura, la respuesta es 403 antes de consultar el almacén.

## Idempotencia (ADR-012)

- **`Idempotency-Key` es obligatoria** en `POST /v1/runs`: 16–64 caracteres `[A-Za-z0-9_-]`; si falta o es inválida → 400 `IDEMPOTENCY_KEY_REQUIRED`.
- **`run_id` determinista:** `run_id = "idem-" + SHA-256("medrixa.idempotency.v1" | sujeto | clave)`. Como la creación del repositorio es atómica, cada clave crea como máximo una ejecución, aunque haya reintentos, concurrencia, varios procesos o reinicios. No se necesita un almacén de claves adicional.

| Reintento con la misma clave | Resultado |
|---|---|
| Mismo cuerpo, ejecución terminada | 201 con la misma vista, `Idempotent-Replayed: true`; no se reejecuta nada |
| Mismo cuerpo, ejecución en curso o interrumpida | 409 `IDEMPOTENCY_IN_PROGRESS` (tras `/v1/admin/recovery`, se repite con `INTERRUPTED`) |
| Cuerpo distinto | 422 `IDEMPOTENCY_KEY_REUSED`; la ejecución original no se toca |
| Otro sujeto | Otra ejecución (las claves se separan por sujeto) |

- **`case.case_id` es obligatorio** en HTTP, para que el cuerpo sea comparable entre reintentos.

## Errores

Todos los errores usan el mismo sobre: `{"api_version":"medrixa.http.v1","error":{"code","message","request_id"}}`.
- Los mensajes son constantes del catálogo (`app/web/errors.py`). Nunca se devuelve la entrada, el texto de una excepción, una ruta ni una traza.
- `request_id` viene de `X-Request-ID` si es válido (`[A-Za-z0-9-]{8,64}`). Si no, se genera uno, lo que evita la inyección de cabeceras.

| Código | HTTP | Código | HTTP |
|---|---|---|---|
| INVALID_INPUT | 400 | IDEMPOTENCY_IN_PROGRESS | 409 |
| IDEMPOTENCY_KEY_REQUIRED | 400 | RUNS_ACTIVE | 409 |
| UNAUTHENTICATED | 401 | PAYLOAD_TOO_LARGE | 413 |
| FORBIDDEN | 403 | UNSUPPORTED_MEDIA_TYPE | 415 |
| NOT_FOUND | 404 | IDEMPOTENCY_KEY_REUSED | 422 |
| METHOD_NOT_ALLOWED | 405 | INTERNAL_ERROR / STORE_INTEGRITY_ERROR | 500 |
| CONFLICT | 409 | STORE_UNAVAILABLE / AUDIT_UNAVAILABLE | 503 |

## Seguridad por defecto

- **Almacén:** se elige con `MEDRIXA_PG_DSN` (PostgreSQL, P5, tratado como secreto) o `MEDRIXA_STORE_DIR` (archivos provisional), que son excluyentes. Sin ninguno, se usa memoria.
- **Configuración solo desde `MEDRIXA_*`.** `MEDRIXA_ENV` debe ser `development` o `test`; `production` se rechaza. `MEDRIXA_AUTH_SECRET` es obligatorio, de al menos 32 bytes y no trivial, y nunca se imprime.
- **Lista de hosts permitidos:** `127.0.0.1` y `localhost`, sin comodines. `serve` se liga solo a 127.0.0.1, sin cabecera `Server` y sin confiar en cabeceras de proxy.
- **Límites de entrada:**
  - Solo se acepta `Content-Type: application/json`.
  - El cuerpo tiene un máximo de 1 MiB, que se aplica tanto por `Content-Length` como mientras se lee un flujo sin longitud.
  - El JSON es estricto: sin claves duplicadas, NaN ni campos extra, y con marca sintética explícita.
- **Cabeceras:** `Cache-Control: no-store`, `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` y CSP `default-src 'none'`.
- **OpenAPI y `/docs`** solo están disponibles en `development`.
- **Auditoría de accesos (`app/web/audit.py`):** registra sujeto, acción, resultado, caso, ejecución y `request_id`. Nunca guarda tokens, cuerpos ni observaciones.
  - Las mutaciones se auditan **antes** de sus efectos.
  - Si el sink falla, la operación se rechaza con `AUDIT_UNAVAILABLE`.
  - Un logger sin configurar también falla cerrado. `serve` configura un manejador de líneas JSON en stderr.

## Límites conocidos

- La ejecución corre dentro de la petición, y la E/S del adaptador de archivos es bloqueante en el bucle de eventos. Es aceptable en local, pero hace falta un ejecutor en segundo plano o un adaptador asíncrono antes de soportar carga.
- `RUNS_ACTIVE` protege la recuperación frente a ejecuciones del **mismo proceso**. **Desde P6**, cada creación se ejecuta bajo un arrendamiento con latido, y `POST /v1/admin/recovery` omite las ejecuciones con arrendamiento vivo de otros procesos: las informa en `active_elsewhere`, un campo presente solo si es mayor que 0. Si un ejecutor pierde su arrendamiento a mitad de ejecución, la respuesta es 409 `CONFLICT` y la ejecución queda para revisión, sin reintento. El TTL se configura con `MEDRIXA_LEASE_TTL_SECONDS` (30 s por defecto, provisional).
- No hay limitación de tasa, CORS ni TLS. El servidor es local; TLS correspondería al proxy en un despliegue, que no está autorizado.
