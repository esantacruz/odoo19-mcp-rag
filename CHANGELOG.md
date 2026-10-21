# Cambios

## 1.1.0 — 2026-10-21

- Instalador Linux con entorno Python aislado y dependencias fijadas en `uv.lock`.
- Servicio systemd de usuario, arranque con el equipo mediante linger y reinicio ante fallos.
- Endpoint MCP Streamable HTTP en loopback, manteniendo el transporte stdio existente.
- Comprobación de salud, disponibilidad y consulta MCP real antes de declarar instalación exitosa.
- Comandos de operación, actualización de fuentes y retirada del servicio conservando los datos.
- Generación de configuraciones portables para clientes y empaquetado sin índices, modelos ni claves.
- Pruebas de reinstalación, errores, rutas con espacios y validación de unidades por systemd.

Verificación local: 24 pruebas aprobadas; instalación real con índice existente, servicio activo
y habilitado, `Linger=yes`, consulta HTTP con fuente oficial y recuperación automática tras
terminación del proceso. El arranque después de un reinicio físico del equipo no se ha ensayado.
El instalador automático de esta versión se limita a Linux con systemd.

## 1.0.0 — 2026-10-20

- Ingesta de documentación oficial funcional y técnica de Odoo 19.0.
- MiniLM local, Chroma persistente, búsqueda híbrida, categorías y citas.
- Herramientas `search_odoo19_docs` y `get_odoo_topic_context` por stdio.
- Indexación incremental y exclusión de tutoriales expresamente obsoletos.
