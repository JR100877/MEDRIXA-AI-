# P7 — siguiente paquete (propuesta de planificación, no fase aprobada)

Base: P6 en la rama `dev/p6-leases`, publicada y sin fusionar. Ver `CONTINUITY.json`.

## Objetivo

Endurecer la seguridad para acercar MEDRIXA a un despliegue controlado, que **sigue sin estar autorizado**: identidad, secretos, cifrado y controles de acceso.

## Bloqueos que requieren decisión humana (Robinson)

1. **Proveedor de identidad para producción:** OIDC con Keycloak, Entra ID, Auth0 u otro. Condiciona cómo se validan los tokens, sus claims (roles y casos) y su revocación. Sin esta decisión no se reemplaza el token HMAC provisional.
2. **Gestión de secretos:** gestor de secretos, KMS o variables gestionadas, y la política de rotación.
3. **Cifrado en reposo:** a nivel de disco o volumen, de base de datos o de aplicación (campos de `ClinicalCase`), y quién custodia las claves.
4. **Valores operativos:** TTL de arrendamiento (hoy 30 s, provisional), vida máxima de los tokens y límites de tasa.

## Trabajo técnico independiente y seguro, sin esas decisiones

- Rotación de la clave HMAC local con `kid` y varias claves válidas a la vez, para desarrollo, con pruebas.
- Limitación de tasa por sujeto y por IP en la API local: puerto más adaptador en memoria.
- Roles de PostgreSQL de mínimo privilegio (migración separada del rol de aplicación), probados contra el clúster efímero.
- Driver o ejecutor asíncrono para que la E/S no bloquee el bucle de eventos.
- Pool de conexiones para PostgreSQL.

## Condiciones

No hay despliegue, datos reales ni infraestructura gestionada sin autorización. Ninguna rama se fusiona sin autorización.
