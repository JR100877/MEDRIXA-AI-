# Seguridad de MEDRIXA AI (estado al cierre de P7)

**MEDRIXA no es apto para producción.** Este documento separa lo que está implementado y probado de lo que solo está diseñado, propuesto o bloqueado por decisiones humanas. Pasar pruebas técnicas no acredita seguridad productiva ni validación clínica.

Leyenda de estado: **PROBADO** (implementado, con pruebas automatizadas reales) · **IMPLEMENTADO** (sin prueba automatizada específica) · **DISEÑADO** · **PROPUESTO** · **BLOQUEADO** (requiere una decisión humana).

## 1. Límites de confianza

```
 [Cliente HTTP] ──(1) TLS en el proxy: requisito de despliegue, NO implementado aquí──┐
                                                                                      ▼
 [Proxy/API gateway: TLS, WAF, límite de tasa compartido]  ← fuera de MEDRIXA (despliegue no autorizado)
                                                                                      │ (2) HTTP en 127.0.0.1
                                                                                      ▼
 ┌───────────────────────── Proceso MEDRIXA (confianza: código propio y agentes locales) ─────────────────────────┐
 │ FastAPI: host allow-list · límite por IP · cuerpo ≤1 MiB · errores uniformes · cabeceras de seguridad           │
 │   └─ Authenticator ──(3) JWKS por HTTPS──► [Proveedor OIDC]  (BLOQUEADO: elección del proveedor)               │
 │ RunService: autorización rol×caso · organización única · idempotencia · auditoría previa a efectos · capacidad │
 │ CORE/Orchestrator/agentes simulados (sin red, sin inferencia clínica)                                          │
 └───────────────┬──────────────────────────────────────┬──────────────────────────────────────────────────────────┘
                 │ (4) rol de aplicación (solo DML)      │ (5) líneas JSON de auditoría (stderr)
                 ▼                                       ▼
        [PostgreSQL: dueño ≠ aplicación,          [Recolector de logs: retención y
         eventos append-only por triggers]          protección, fuera de MEDRIXA]
```

| Límite | Qué cruza | Control en MEDRIXA | Responsabilidad externa |
|---|---|---|---|
| (1) Cliente → proxy | Tokens, casos sintéticos | — | TLS 1.2+ y certificados (requisito) |
| (2) Proxy → proceso | Peticiones | Host allow-list, límites, validación estricta, sin confiar en `X-Forwarded-*` | El proxy debe ser el único acceso; el servidor se liga a 127.0.0.1 |
| (3) Proceso → IdP | JWKS | HTTPS obligatorio (HTTP solo loopback), sin redirecciones, tamaño y tiempo acotados | Disponibilidad y custodia de las claves del IdP |
| (4) Proceso → PostgreSQL | Instantáneas y eventos | Rol de mínimo privilegio, CAS, verificación en lectura | TLS hacia la base, cifrado del volumen, respaldos |
| (5) Proceso → logs | Auditoría sin carga clínica | Falla cerrada si no puede registrar | Retención, inmutabilidad y acceso a logs |

## 2. Amenazas (STRIDE) y controles

| Amenaza | Ejemplo | Control | Estado |
|---|---|---|---|
| **S**uplantación | Token falsificado, confusión de algoritmo, clave de atacante con el mismo `kid`, `alg: none` | OIDC con PyJWT; solo algoritmos asimétricos permitidos; `kid` obligatorio; `alg` de la JWK fijado; iss/aud/exp/iat/sub obligatorios | PROBADO |
| Suplantación | Token de otra organización | Claim de organización debe coincidir | PROBADO |
| Suplantación | Robo de token vigente | Vida máxima acotada (≤1 h por defecto) | IMPLEMENTADO; revocación BLOQUEADA (depende del IdP) |
| **T**ampering | Alterar eventos en la base | Triggers append-only; el rol de aplicación no es dueño y no puede desactivarlos | PROBADO |
| Tampering | Alterar instantáneas | SHA-256 + contraste con columnas y eventos en cada lectura | PROBADO (detecta, no impide al dueño de la base) |
| Tampering | Reescribir historial vía API | CAS de una transición que solo extiende el historial | PROBADO |
| **R**epudio | Negar una operación | Auditoría con sujeto, organización, `request_id`, previa a efectos; falla cerrada | PROBADO; firma e inmutabilidad del log: PROPUESTO |
| **I**nformation disclosure | Acceso a ejecuciones de otro caso | Autorización por caso; 404 indistinguible | PROBADO |
| Information disclosure | Fugas en errores o logs | Catálogo de errores constante; DSN y secretos fuera de errores y `repr`; auditoría sin observaciones | PROBADO |
| Information disclosure | Secretos en el código o la imagen | Puerto de secretos; archivos montados (`*_FILE`) con permisos verificados | PROBADO |
| Information disclosure | Datos en reposo | — | BLOQUEADO (cifrado en reposo, ver §4) |
| **D**enial of service | Ráfagas, fuerza bruta de credenciales | Límite por IP (antes de autenticar) y por identidad; 429 + `Retry-After` | PROBADO (por proceso) |
| DoS | `kid` aleatorios para forzar descargas de JWKS | Refetch como máximo una vez por intervalo | PROBADO |
| DoS | Saturar ejecuciones | Tope de ejecuciones simultáneas reservado antes de crear; 503 | PROBADO |
| DoS | Cuerpos enormes o JSON profundo | 1 MiB con y sin `Content-Length`; JSON estricto | PROBADO |
| DoS | Agotar conexiones a la base | Pool acotado; espera máxima = `connect_timeout` | PROBADO |
| **E**levation of privilege | Rol inexistente en el token | Roles desconocidos se ignoran; sin rol conocido → 401 | PROBADO |
| Elevation | Operador que lee casos | El operador solo recupera | PROBADO |
| Elevation | Aplicación que escala en la base | Solo DML concedido; sin `CREATE`, `ALTER` ni re-`GRANT` efectivo | PROBADO |
| Elevation | Decisión clínica automática | No existe permiso ni endpoint de aprobación; revisión humana terminal | PROBADO |

## 3. Requisitos de despliegue

Estos requisitos **no están implementados en MEDRIXA** y bloquean cualquier despliegue, que además no está autorizado:

1. **TLS:** terminación TLS 1.2+ en un proxy o gateway delante de MEDRIXA. El proceso solo escucha en 127.0.0.1 y no confía en `X-Forwarded-*`. La conexión a PostgreSQL también debe usar TLS (`sslmode=verify-full`) cuando no sea un socket local.
2. **Identidad:** `MEDRIXA_AUTH_MODE=oidc` con un proveedor real. El token HMAC solo sirve para desarrollo y pruebas.
3. **Secretos:** se entregan por archivo montado (`MEDRIXA_*_FILE`) o mediante un adaptador del gestor elegido. Las variables de entorno con valores secretos solo se admiten en desarrollo.
4. **Base de datos:** las migraciones las aplica el rol dueño (`python -m app.persistence.pg_admin migrate`), y la aplicación usa un rol con `MEDRIXA_PG_MIGRATE=false` y los privilegios de `pg_admin grant`.
5. **Límite de tasa compartido:** con más de una réplica, en el proxy o mediante un adaptador compartido. El limitador actual es por proceso.
6. **Auditoría:** el recolector debe capturar stderr del proceso, con retención e integridad definidas por la organización.
7. **Cifrado en reposo** y **respaldos:** BLOQUEADO (§4).

## 4. Decisiones bloqueadas: opciones y recomendación

| Decisión | Opciones | Recomendación técnica | Por qué está bloqueada |
|---|---|---|---|
| Proveedor de identidad | Keycloak (autoalojado), Microsoft Entra ID, Auth0/Okta, AWS Cognito | Cualquiera que emita JWT RS256/ES256 con JWKS y claims configurables. El adaptador ya es genérico | Contrato, costo y jurisdicción de datos |
| Gestión de secretos | Archivos montados de la plataforma, HashiCorp Vault, gestor de la nube | Empezar con archivos montados por la plataforma (ya soportados) y añadir un adaptador si se elige un gestor | Custodia y proveedor |
| Cifrado en reposo | Volumen o disco (plataforma), TDE de la base, cifrado de campos en la aplicación | Volumen cifrado por la plataforma más TLS. El cifrado de campos solo si un análisis de riesgo lo exige, por su costo en búsquedas y rotación | Custodia de claves y requisitos regulatorios |
| Multi-tenancy | Columna de organización + RLS de PostgreSQL, esquema por organización, base por organización | RLS con `organization_id` en ejecuciones y arrendamientos, más pruebas de aislamiento cruzado obligatorias antes de habilitarlo | Cambio de contrato de dominio y modelo de aislamiento |
| Valores operativos | TTL de arrendamiento, vida de tokens, límites de tasa | TTL de 30 s (margen de unas mil veces sobre la latencia medida, `evidence/P7_latency.json`), tokens ≤1 h y límites según la carga real medida | Dependen de la operación real |

## 5. Limitaciones conocidas

- La organización está **forzada a una sola** en modo OIDC; en modo HMAC no hay organización. El modelo de datos no tiene columna de organización todavía.
- El limitador de tasa y el tope de capacidad valen **por proceso**.
- `psycopg_pool` registra en el logger `psycopg.pool` los errores de conexión de libpq, que pueden incluir host o socket, usuario y base, nunca la contraseña. Conviene encaminarlo a logs operativos y no a logs públicos.
- La verificación `crit` de MEDRIXA es redundante con la de PyJWT 2.15, que ya rechaza extensiones críticas desconocidas. Se mantiene como defensa en profundidad.
- Un dueño de la base de datos puede desactivar triggers y reescribir instantáneas y eventos de forma coherente. Solo una firma o un almacenamiento externo inmutable lo impediría (PROPUESTO).
- **Dependencias:** las 26 versiones fijadas en `requirements-lock.txt` se auditaron con pip-audit 2.10.1 contra el servicio de vulnerabilidades de PyPI el 16/10/2026, sin vulnerabilidades conocidas (`evidence/P7_dependency_audit.json`). Es una foto de esa fecha: debe repetirse periódicamente. La auditoría no cubre hashes, porque el bloqueo no los fija.
- No hubo auditoría de seguridad externa, pruebas de penetración ni prueba con un IdP real.
