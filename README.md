# Odoo 19 MCP RAG

Servidor MCP local sobre documentación **funcional y de desarrollo de Odoo 19.0**, con
`FastMCP("Odoo19Docs")` del SDK oficial, Chroma persistente y MiniLM en CPU. Recupera evidencia
citada; el LLM del cliente redacta la respuesta. No requiere una API de generación.

## Instalador con arranque automático (Linux)

Descargue y extraiga el código fuente completo, entre a la carpeta y ejecute:

```bash
git clone https://github.com/esantacruz/odoo19-mcp-rag.git
cd odoo19-mcp-rag
bash install.sh
```

El instalador crea el entorno, descarga e indexa las fuentes oficiales y habilita un servicio
de usuario de systemd que arranca con el equipo y se reinicia ante fallos. Al finalizar, el
endpoint MCP es **`http://127.0.0.1:8019/mcp`**, accesible desde clientes locales por Streamable HTTP.
Valida el servicio con una consulta MCP real antes de informar éxito.

La instalación normal conserva el programa y el RAG en `~/.local/share/odoo19-mcp-rag/`:
el índice, las fuentes y el modelo quedan dentro de `data/`. No depende de la carpeta desde
la que se clonó el proyecto. `--use-existing-data` es una excepción explícita: utiliza la
carpeta indicada sin copiarla y mantiene esa dependencia hasta que se migren los datos.

Consulte [instalación, configuración de clientes y desinstalación](docs/INSTALLATION.md).
El transporte `stdio` sigue disponible para clientes que inician su propio proceso.

## Instalación manual para desarrollo

Requisitos: Git, `uv`, Python 3.11–3.13 e Internet para la instalación inicial.
Se utiliza Python 3.12, fijado en `.python-version`.

```bash
cd odoo19-mcp-rag
uv sync --locked --extra dev
uv run python -m src.ingest
uv run python -m src.status
uv run python -m src.server
```

La primera ingesta descarga la rama oficial `19.0`, el modelo y el tokenizador. El clon usa
`--depth 1`, `--filter=blob:none` y sparse checkout de texto, sin imágenes ni traducciones `.po`.
Los registros van a stderr. El servidor reserva stdout al protocolo y queda esperando mensajes
MCP: no presenta una consola interactiva. En modo stdio el cliente inicia y detiene el proceso.
Después de instalar también se admiten:

```bash
.venv/bin/python -m src.server
uv run src/server.py
```

El SDK se limita a `mcp>=1.26,<2` para conservar la API oficial FastMCP solicitada. `uv.lock` fija
las versiones probadas. `uv` selecciona PyTorch CPU en Linux/Windows. En otro equipo, reconstruya
el entorno y regenere las rutas del cliente.

## Estructura

```text
data/raw/documentation/  clon oficial, rama 19.0
data/chroma_db/          Chroma + SQLite FTS5 + manifiesto de integridad
data/models/             caché del modelo y tokenizador
src/config.py           rutas, parámetros y compatibilidad del índice
src/parsing.py          lector RST/Markdown y chunking
src/embeddings.py       MiniLM local / OpenAI opcional
src/ingest.py           descarga e indexación incremental
src/storage.py          persistencia y bloqueo de mantenimiento
src/retriever.py        búsqueda híbrida y contexto
src/server.py           herramientas FastMCP por stdio o HTTP local
src/probe.py            prueba real de la conexión MCP HTTP
scripts/install.py      instalación y arranque con systemd de usuario
src/status.py           comprobación de integridad y categorías
src/client_config.py    configuración con rutas absolutas
src/evaluate.py         evaluación de recuperación y latencia
tests/                  pruebas de datos y protocolo real
reports/                resultados de validación de esta instalación
mcp_config_example.json ejemplo portable; sustituir las rutas
```

## Herramientas

`search_odoo19_docs(query: str, max_results: int = 4, category: str | None = None) -> str`

- Acepta 1–12 resultados y consultas de 1–2000 caracteres.
- Filtra antes de ordenar en ambos índices. `developer` abarca desarrollo y `applications`
  funcional. Categorías específicas: `accounting`, `inventory`, `purchase`, `studio`, `sales`,
  `crm`, `manufacturing`, entre otras. `src.status` muestra las categorías reales.
- Admite alias `contabilidad`, `inventario`, `desarrollo`, `compras`, `ventas`, `funcional`.
- Devuelve títulos, texto/código, categoría, URL oficial y enlace al commit exacto. Una categoría
  desconocida devuelve cero resultados.

`get_odoo_topic_context(topic: str) -> str`

- Busca el tema y variantes sobre ejemplos/API y restricciones/dependencias/seguridad.
- Fusiona evidencia y limita la concentración por archivo: máximo ocho fragmentos y 6400 tokens
  de contenido; las cabeceras/citas agregan algo al presupuesto.
- Ejemplos: `OWL hooks`, `computed fields dependencies`, `record rules syntax`.
- El LLM cliente realiza la síntesis. Esta herramienta no llama a otro LLM.

## Procesamiento y búsqueda

1. El lector conserva títulos y código Python/XML/JavaScript/OWL. Expande includes locales de
   texto dentro de `content/`, sin ejecutar Sphinx, scripts ni imports. Señala recursos externos
   no disponibles. Los includes con selectores de líneas se conservan completos y pueden aportar
   contexto adicional.
2. Usa `cl100k_base`, máximo 800 tokens y solapamiento de prosa cercano a 150. Respeta secciones
   y bloques de código; un bloque mayor se divide preferentemente por líneas y se reabren sus
   cercas Markdown. El solapamiento no corta bloques de código. Secciones cortas producen
   fragmentos menores y pueden no necesitar solapamiento.
3. MiniLM produce 384 dimensiones y admite 256 wordpieces, distintos de los tokens de chunking.
   Se procesan **todas las ventanas** del tokenizador real y se promedian sus vectores normalizados,
   ponderados por su número de tokens; se vuelve a normalizar el resultado. Así se evita truncar
   silenciosamente los fragmentos largos.
4. Chroma usa distancia coseno y la colección `odoo19_docs`. SQLite FTS5 aporta coincidencias de
   identificadores exactos. Se fusionan las clasificaciones mediante RRF (`k=60`), con máximo
   dos fragmentos por sección en la búsqueda simple.
5. El proceso reutiliza el modelo y conserva hasta 128 embeddings de consulta en memoria.

Metadatos: `file_path`, `headings`, `title`, `category`, `area`, `url`, `source_url`, `source_commit`,
`source_hash`, `tokens`, `chunk_index`, `version`, `language`, `edition`.

## Alcance Enterprise, idioma y límites de las fuentes

La fuente es [odoo/documentation, rama 19.0](https://github.com/odoo/documentation/tree/19.0).
Se excluyen páginas que Odoo marca expresamente como obsoletas y contratos traducidos en
`legal/terms/i18n/`. Las fuentes originales permanecen en el clon; el manifiesto y `src.status`
registran cada exclusión y su motivo. No se excluyen páginas solo por tener la marca `:orphan:`.
Incluye documentación pública funcional y técnica, también sobre aplicaciones Enterprise.
**No equivale al código privado de Enterprise ni garantiza todas sus APIs internas.** Se utiliza
`edition=unspecified` para evitar atribuir exclusividad sin evidencia. El índice público no
requiere una suscripción Enterprise.

Sphinx completa algunas referencias ORM con docstrings de `odoo/odoo` y `odoo/upgrade-util`.
Este lector conserva la prosa disponible y señala `autoclass`/`automethod` sin expandir; no descarga
ni ejecuta esos repositorios. Tampoco hace OCR ni indexa contenido remoto enlazado. Véanse las
[instrucciones oficiales de compilación](https://github.com/odoo/documentation/tree/19.0#using-local-odoo-sources).

El corpus y MiniLM son principalmente ingleses. Para mayor precisión, el cliente debe convertir
la pregunta española a términos técnicos ingleses antes de buscar y responder después en español.
Los alias de categorías no traducen automáticamente las consultas.

## Clientes MCP

Genere la configuración después de instalar o mover el proyecto:

```bash
.venv/bin/python -m src.client_config --output mcp_config_local.json
```

El JSON contiene el Python del entorno, script y datos mediante rutas absolutas, sin claves.
El script absoluto permite funcionar aunque el cliente ignore `cwd`. Agregue la entrada
`odoo19-docs` al objeto `mcpServers` existente y conserve las demás entradas.

| Cliente | Configuración |
| --- | --- |
| Claude Desktop | Settings → Developer → Edit Config; agregar la entrada y reiniciar. |
| Cursor | Agregarla a `.cursor/mcp.json` del proyecto o `~/.cursor/mcp.json`. |
| Codex | Convertirla a `[mcp_servers.odoo19-docs]` en TOML o usar los ajustes MCP. |
| ChatGPT Desktop | Depende de las funciones habilitadas en su versión/cuenta; no se presupone que lea el JSON de Claude. La ejecución local habilitada usa stdio; la conexión remota requiere un adaptador/túnel compatible. |

Referencias: [MCP local/Claude](https://modelcontextprotocol.io/docs/develop/connect-local-servers),
[Cursor](https://cursor.com/docs/context/mcp), [MCP de OpenAI](https://learn.chatgpt.com/docs/extend/mcp),
[conexión e instalación en ChatGPT](https://developers.openai.com/plugins/deploy/connect-chatgpt).
El proyecto admite **stdio y Streamable HTTP local**. El instalador arranca el servicio HTTP
automáticamente; las cuentas y las conexiones en cada cliente se configuran por separado.

## Configuración y OpenAI opcional

Copie `.env.example` a `.env` para modificar valores. Se lee desde la raíz del proyecto y
las variables ya presentes en el proceso prevalecen.

| Variable | Valor predeterminado |
| --- | --- |
| `ODOO_RAG_DATA_DIR` | `data`, relativo al proyecto |
| `ODOO_RAG_EMBEDDING_PROVIDER` | `local` |
| `ODOO_RAG_LOCAL_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` |
| `ODOO_RAG_LOCAL_REVISION` | commit fijado en `config.py` y `.env.example` |
| `ODOO_RAG_CHUNK_SIZE` / `ODOO_RAG_CHUNK_OVERLAP` | `800` / `150` |
| `ODOO_RAG_BATCH_SIZE` / `ODOO_RAG_TORCH_THREADS` | `32` / `4` |
| `ODOO_RAG_OFFLINE` | `true` al servir |

Cambiar proveedor, modelo, revisión, chunking o versión del lector exige reconstruir el índice.
OpenAI es una opción explícita, de pago; la existencia de una clave no la activa:

```bash
uv sync --locked --extra openai
export ODOO_RAG_EMBEDDING_PROVIDER=openai
export OPENAI_API_KEY="su-clave"
uv run python -m src.ingest --force-reindex
uv run python -m src.server
```

Usa `text-embedding-3-small` con 1536 dimensiones por defecto, tiempo de espera y reintentos
acotados. Este modo envía consultas y fragmentos a OpenAI. Use el mismo proveedor al indexar y
servir y ajuste `env` en el cliente. Véase [OpenAI Docs: embeddings](https://developers.openai.com/api/docs/guides/embeddings).

## Actualización, recuperación y operación

```bash
.venv/bin/python -m src.ingest                     # actualizar fuentes e índice
.venv/bin/python -m src.ingest --skip-download     # reusar el clon existente
.venv/bin/python -m src.ingest --offline           # sin descargas de modelo ni fuentes
.venv/bin/python -m src.ingest --force-reindex --offline
.venv/bin/python -m src.status                    # integridad, fecha, commit y categorías
```

Se omiten archivos iguales por hash del contenido expandido y se eliminan fragmentos obsoletos
o de archivos borrados. Cada archivo completado queda registrado atómicamente. Una interrupción
deja el estado `indexing`: las consultas se rechazan hasta reanudar la orden. Un árbol de fuentes
vacío se rechaza antes de borrar datos. Un error de integridad exige restaurar o reconstruir.

Consultas e ingesta comparten bloqueo de archivo entre procesos. Durante mantenimiento las
consultas devuelven un error temporal. Detenga los clientes antes de reconstruir y reinícielos
al terminar. Use **un proceso MCP activo por base de datos** en este despliegue embebido; para
varios clientes simultáneos asigne directorios de datos separados. Un proceso abierto detecta
el cambio de generación del índice y pide reiniciarse antes de continuar. No se ofrece disponibilidad
sin interrupciones durante una reconstrucción.

Para respaldar, detenga servidor e ingesta y copie **todo `data/`**, con Chroma, SQLite,
manifiesto, fuentes y modelo. Conserve también `uv.lock` y `.env` por separado. Restaure todo con
la misma configuración y ejecute `src.status`. La actualización automática no está programada:
ejecute la ingesta cuando requiera cambios de la rama 19.0. Un archivo sin cambios puede citar
su commit anterior, que sigue siendo una referencia exacta válida de ese contenido.

## Pruebas y mediciones

Validación de esta instalación, 20 de octubre de 2026, con Python 3.12.14 y MiniLM en CPU:

| Comprobación | Resultado |
| --- | --- |
| Archivos fuente descargados | 1162 |
| Archivos activos / fragmentos | 1137 / 9006 |
| Exclusiones documentadas | 25: tres tutoriales obsoletos y 22 contratos traducidos |
| Suite completa, incluido cliente MCP real | 16 pruebas aprobadas |
| Casos de recuperación con ruta relevante entre cuatro resultados | 10/10 |
| Primera consulta, incluida carga del modelo | 2,98 s |
| Mediana de consultas nuevas, excluida la primera | 21,55 ms |
| Mediana / percentil 95 con embedding en caché | 14,98 / 18,94 ms |

Los informes de evaluación, integridad y pruebas se generan en `reports/` y se excluyen de la
distribución pública para no publicar rutas o datos de la máquina. La búsqueda por stdio fue
verificada con las dos herramientas.
La integración en interfaces gráficas específicas de los clientes no se ha realizado desde este proyecto.
La opción OpenAI se probó con respuestas simuladas; las mediciones anteriores usan el modelo local real.

```bash
uv sync --locked --extra dev
.venv/bin/ruff check src tests
.venv/bin/python -m pytest -m "not integration" -q
# Requiere índice y modelo instalados:
.venv/bin/python -m pytest -q
.venv/bin/python -m src.evaluate --output reports/retrieval-evaluation.json
```

Las pruebas de almacenamiento usan vectores deterministas aislados; las de integración usan el
modelo real y un cliente MCP por stdio. Cubren código/Unicode, includes, presupuesto/solapamiento,
incremento/borrado/reconstrucción, fallos/reanudación, filtros, validación, citas y las herramientas.

La evaluación usa diez consultas inglesas predefinidas y comprueba rutas relevantes entre los
cuatro primeros resultados (`hit@4`). Distingue carga inicial, consultas nuevas y embeddings en
caché. Es una prueba acotada, no mide exactitud de respuestas finales ni garantiza precisión
para todas las preguntas/idiomas. El informe JSON conserva consultas y rutas para revisión.
Si el entorno bloquea los pipes de subprocesos, repita las pruebas stdio en una terminal local
con permisos para esa comunicación.

## Atribución

Documentación de Odoo S.A. y colaboradores bajo [CC BY-SA 4.0](https://github.com/odoo/documentation/blob/19.0/LICENSE).
Los fragmentos adaptan formato/chunking y conservan enlaces de atribución. El modelo tiene
[licencia Apache 2.0](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2).
Los datos y credenciales están excluidos de Git; al redistribuir el índice conserve atribución
y condiciones de las fuentes.
