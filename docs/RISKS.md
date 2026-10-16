# Riesgos y límites al cierre de P5

| Riesgo | Control actual | Pendiente |
|---|---|---|
| Atribuir código nuevo al histórico | Área, rama y procedencia explícitas; originales sin modificar | Comparación si aparecen originales |
| Datos reales etiquetados como sintéticos | Marcador obligatorio, sin campos identificadores, demo artificial | El prototipo no detecta PHI; no introducirla. Los registros del adaptador local contienen el caso sintético completo |
| Inferencia médica sin validar | Agentes solo comprueban presencia; revisión humana terminal | Protocolos científicos, expertos y regulación antes de uso clínico |
| Agente defectuoso/bloqueante | Registro explícito, límite de pasos, timeout y rechazo de resultados tardíos | Aislamiento en procesos para código no confiable; timeout actual es cooperativo |
| Caída de proceso | Cada transición se persiste antes de publicarse; tras un reinicio, la recuperación lleva lo interrumpido a revisión `INTERRUPTED` sin reejecutar agentes. Probado con SIGKILL real en el adaptador de archivos y en PostgreSQL (P5) | El efecto de un agente en vuelo queda desconocido y debe revisarlo una persona |
| Recuperación concurrente con ejecutor vivo | CAS: el ejecutor falla con `RevisionConflict` y nunca sobrescribe | Sin arrendamiento/latido: ejecutar la recuperación al arranque, sin ejecutores activos |
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
