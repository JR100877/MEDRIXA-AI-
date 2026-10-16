# Riesgos y límites al cierre de P4

| Riesgo | Control actual | Pendiente |
|---|---|---|
| Atribuir código nuevo al histórico | Área, rama y procedencia explícitas; originales sin modificar | Comparación si aparecen originales |
| Datos reales etiquetados como sintéticos | Marcador obligatorio, sin campos identificadores, demo artificial | El prototipo no detecta PHI; no introducirla. Los registros del adaptador local contienen el caso sintético completo |
| Inferencia médica sin validar | Agentes solo comprueban presencia; revisión humana terminal | Protocolos científicos, expertos y regulación antes de uso clínico |
| Agente defectuoso/bloqueante | Registro explícito, límite de pasos, timeout y rechazo de resultados tardíos | Aislamiento en procesos para código no confiable; timeout actual es cooperativo |
| Caída de proceso | P4: cada transición se persiste antes de publicarse (CAS, escritura atómica); al reiniciar, `--recover` lleva lo interrumpido a revisión con `INTERRUPTED` sin reejecutar agentes. Probado con SIGKILL real | Solo con el adaptador local provisional; PostgreSQL pendiente. El efecto de un agente en vuelo queda desconocido y debe revisarlo una persona |
| Recuperación concurrente con ejecutor vivo | CAS: el ejecutor falla con `RevisionConflict` y nunca sobrescribe | Sin arrendamiento/latido: ejecutar la recuperación al arranque, sin ejecutores activos |
| Alteración de eventos | Validación de secuencia/correlación, snapshots inmutables, guardado que solo admite extender historial, SHA-256 de corrupción accidental | Sin firma ni almacenamiento append-only externo: quien escribe el directorio puede falsificar un registro |
| Concurrencia entre procesos | RLock en proceso; en almacenamiento, bloqueo exclusivo por ejecución (`flock`) + CAS. Probado con 8 hilos y 4 procesos | Sin bloqueo multi-servidor ni sistemas de archivos de red; Windows (`msvcrt.locking`) no probado |
| Registro corrupto | Se detecta, se informa y nunca se modifica, repara ni borra | Procedimiento operativo de cuarentena/restauración desde respaldo |
| Datos en reposo | Permisos 0600/0700 en POSIX | Sin cifrado en reposo, retención ni borrado seguro |
| Información en logs | Eventos de códigos/IDs; resúmenes de ejecución y recuperación sin payload ni IDs; errores públicos uniformes | IDs suministrados también deben ser sintéticos; no hay filtro universal de información personal |
| Exposición de servicio | No existe servidor ni conexiones externas en la app | AuthN/AuthZ, cifrado, secretos y revisión productiva antes de desplegar |
| Cambios de dependencias | Versiones registradas; P4 no añade dependencias | No se hizo auditoría de vulnerabilidades ni prueba de instalación en Windows |

La validación estructural de un snapshot detecta inconsistencias, no prueba que su autor sea confiable. Código Python dentro del mismo proceso puede eludir mecanismos de inmutabilidad; solo se admiten agentes locales de confianza.

No hay diagnóstico, tratamiento, cumplimiento regulatorio, certificación ni porcentaje de eficacia clínica acreditados. Pasar pruebas técnicas no acredita ninguna de esas propiedades.
