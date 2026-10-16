# Riesgos y límites al cierre de P3

| Riesgo | Control actual | Pendiente |
|---|---|---|
| Atribuir código nuevo al histórico | Área, rama y procedencia explícitas; originales sin modificar | Comparación si aparecen originales |
| Datos reales etiquetados como sintéticos | Marcador obligatorio, sin campos identificadores, demo artificial | El prototipo no detecta PHI; no introducirla |
| Inferencia médica sin validar | Agentes solo comprueban presencia; revisión humana terminal | Protocolos científicos, expertos y regulación antes de uso clínico |
| Agente defectuoso/bloqueante | Registro explícito, límite de pasos, timeout y rechazo de resultados tardíos | Aislamiento en procesos para código no confiable; timeout actual es cooperativo |
| Caída de proceso | Código y evidencia persistidos | Estado de ejecuciones solo en memoria; P4 debe aportar persistencia transaccional |
| Alteración de eventos | Validación de secuencia/correlación y snapshots inmutables | No hay firma, almacenamiento append-only externo ni auditoría inviolable |
| Concurrencia entre procesos | RLock y revisión optimista dentro del proceso | Compare-and-swap transaccional distribuido pendiente |
| Información en logs | Eventos de códigos/IDs y resumen sin payload; errores públicos uniformes | IDs suministrados también deben ser sintéticos; no hay filtro universal de información personal |
| Exposición de servicio | No existe servidor ni conexiones externas en la app | AuthN/AuthZ, cifrado, secretos y revisión productiva antes de desplegar |
| Cambios de dependencias | Versiones registradas y bloqueo de entorno | No se hizo auditoría de vulnerabilidades ni prueba de instalación en Windows |

La validación estructural de un snapshot detecta inconsistencias, no prueba que su autor sea confiable. Código Python dentro del mismo proceso puede eludir mecanismos de inmutabilidad; solo se admiten agentes locales de confianza.

No hay diagnóstico, tratamiento, cumplimiento regulatorio, certificación ni porcentaje de eficacia clínica acreditados. Pasar pruebas técnicas no acredita ninguna de esas propiedades.
