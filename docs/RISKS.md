# Riesgos y límites al cierre de P7

| Riesgo | Control actual | Pendiente |
|---|---|---|
| Atribuir código nuevo al histórico | Área, rama y procedencia explícitas; originales sin modificar | Comparación si aparecen originales |
| Datos reales etiquetados como sintéticos | Marcador obligatorio, sin campos identificadores, demo artificial | El prototipo no detecta PHI; no introducirla. Los registros del adaptador local contienen el caso sintético completo |
| Inferencia médica sin validar | Agentes solo comprueban presencia; revisión humana terminal | Protocolos científicos, expertos y regulación antes de uso clínico |
| Agente defectuoso/bloqueante | Registro explícito, límite de pasos, timeout y rechazo de resultados tardíos | Aislamiento en procesos para código no confiable; timeout actual es cooperativo |
| Caída de proceso | Cada transición se persiste antes de publicarse; tras un reinicio, la recuperación lleva lo interrumpido a revisión `INTERRUPTED` sin reejecutar agentes. Probado con SIGKILL real en el adaptador de archivos y en PostgreSQL (P5) | El efecto de un agente en vuelo queda desconocido y debe revisarlo una persona |
| Recuperación concurrente con ejecutor vivo | CAS + inmutabilidad terminal (seguridad). P6: arrendamientos con latido y guarda de escritura; la recuperación omite ejecuciones con arrendamiento vivo. Probado con SIGKILL en archivo y PostgreSQL | En archivo y memoria, el arrendamiento depende del reloj de pared de un solo equipo. Una pausa larga sin renovar detiene la ejecución, que queda para revisión. TTL por defecto provisional (30 s) |
| Alteración de eventos | Validación de secuencia y correlación, guardado que solo extiende el historial, SHA-256. En PostgreSQL (P5): eventos append-only forzados por triggers y contraste instantánea/eventos en cada lectura | Sin firma criptográfica. Un propietario de la base puede desactivar los triggers (se detecta al leer si la instantánea y los eventos difieren, no si ambos se falsifican de forma coherente). Con archivos, quien escribe el directorio puede falsificar un registro |
| Concurrencia entre procesos | RLock en proceso; en almacenamiento, bloqueo exclusivo por ejecución (`flock`) + CAS. Probado con 8 hilos y 4 procesos | Sin bloqueo multi-servidor ni sistemas de archivos de red; Windows (`msvcrt.locking`) no probado |
| Registro corrupto | Se detecta, se informa y nunca se modifica, repara ni borra | Procedimiento operativo de cuarentena/restauración desde respaldo |
| Datos en reposo | Permisos 0600/0700 en POSIX | Sin cifrado en reposo, retención ni borrado seguro |
| Información en logs | Eventos de códigos/IDs; resúmenes de ejecución y recuperación sin payload ni IDs; errores públicos uniformes | IDs suministrados también deben ser sintéticos; no hay filtro universal de información personal |
| Exposición de servicio | P4b: servidor solo en 127.0.0.1, lista de hosts, sin perfil productivo, `/docs` solo en desarrollo | TLS, limitación de tasa, CORS y revisión productiva antes de cualquier despliegue (no autorizado) |
| Identidad débil | Puerto `Authenticator`; token HMAC con expiración, audiencia y claims estrictos; secreto ≥32 bytes obligatorio | Secreto compartido sin revocación ni rotación; un token robado sirve hasta expirar (≤8 h). OIDC/IdP en P7 |
| Acceso cruzado entre casos | Autorización por caso con denegación por defecto; 404 indistinguible; probado con 8 defectos deliberados | Revisión de seguridad externa; alcance por caso viaja en el token |
| Duplicación por reintentos | Idempotencia por `run_id` determinista + creación atómica; probado con concurrencia real y reinicio | Cuerpos de error no se reproducen (solo las ejecuciones terminadas) |
| Pérdida de auditoría | Auditoría previa a efectos; sink y logger sin configurar fallan cerrados | Sin almacén append-only ni retención; el destino depende de la configuración de logging |
| Credenciales de base de datos | `MEDRIXA_PG_DSN` solo por entorno, tratado como secreto, nunca en errores ni `repr` | Gestión de secretos, TLS hacia la base y roles de mínimo privilegio no implementados ni probados (P7) |
| Bloqueo del servidor | Ejecuciones sintéticas cortas | Ejecución dentro de la petición y E/S de archivo bloqueante en el bucle de eventos: requiere ejecutor en segundo plano o adaptador asíncrono antes de carga real. El adaptador PostgreSQL abre una conexión por operación (sin pool) |
| Cambios de dependencias | Versiones registradas; P4 no añade dependencias | No se hizo auditoría de vulnerabilidades ni prueba de instalación en Windows |

La validación estructural de un snapshot detecta inconsistencias, no prueba que su autor sea confiable. Código Python dentro del mismo proceso puede eludir mecanismos de inmutabilidad; solo se admiten agentes locales de confianza.

No hay diagnóstico, tratamiento, cumplimiento regulatorio, certificación ni porcentaje de eficacia clínica acreditados. Pasar pruebas técnicas no acredita ninguna de esas propiedades.

## Cambios de riesgo en P7

| Riesgo | Antes | Tras P7 |
|---|---|---|
| Identidad débil (HMAC compartido) | Único modo | OIDC/JWT probado con JWKS real local; HMAC solo para desarrollo y pruebas. Sigue pendiente un IdP real (BLOQUEADO) |
| Dueño de la base desactiva los triggers | Abierto (P5) | **Mitigado para el rol de aplicación** (no es dueño). Sigue abierto para el rol dueño o DBA |
| Secretos en variables de entorno | Única vía | Archivos montados `*_FILE` con permisos verificados |
| Abuso por volumen | Sin límites | 429 por IP y por identidad; tope de ejecuciones (por proceso) |
| Latencia de conexión a la base | Sin medir | Medida; pool con validación de conexión |
| Dependencias vulnerables | Sin auditar | pip-audit sin hallazgos el 16/10/2026 (repetir periódicamente) |
| Accesos entre organizaciones | No modelado | Organización única forzada en OIDC; multi-tenancy BLOQUEADO hasta decidir el modelo y probar el aislamiento |
| Logs de `psycopg.pool` | — | Pueden incluir host, usuario y base de datos (nunca contraseñas); encaminar a logs operativos |

Detalle completo, STRIDE y límites de confianza en `docs/SECURITY.md`.

## Riesgos evaluados en el experimento P9 (no adoptado; `docs/P9_EXPERIMENT.md`)

| Riesgo | Hallazgo | Estado |
|---|---|---|
| Bucle HTTP bloqueado por E/S síncrona | La opción C lo resuelve de forma medible: retraso p99 −80 % a c=8 y latido del arrendamiento aislado | Prototipo experimental, no conectado por defecto |
| Agentes con objetos asyncio compartidos | Con C se cuelgan o terminan en `TIMEOUT` (4 pruebas existentes) | Regla de contrato de agentes necesaria si se adopta C |
| Contabilidad de capacidad al cancelar (C) | La plaza se libera antes de que el hilo termine y se admiten más ejecuciones de las que hay en curso | **Defecto conocido del prototipo**; corrección propuesta, no aplicada |
| Cancelación por el cliente (C) | La ejecución no se cancela; el reintento la reproduce de forma idempotente | Semántica a aceptar o rechazar |
| Apagado (C) | Espera a los hilos; el límite es el timeout cooperativo | Plazo de apagado necesario si se adopta |
| Techo de rendimiento | CPU/GIL (~13 ms de CPU y 36 sentencias por ejecución), no conexiones | Reducir viajes (requiere autorización) o usar varios procesos (opción B) |
