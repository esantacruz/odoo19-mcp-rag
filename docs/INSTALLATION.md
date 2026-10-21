# Instalador Linux y servicio automático

El instalador 1.1.0 requiere Linux con systemd, una sesión de usuario normal, Git y Python 3.11+.
Descargue el código fuente completo, extraiga el paquete y ejecute desde su carpeta:

```bash
bash install.sh
```

No ejecute el instalador con `sudo`. Se utiliza su usuario, no se instala software como root.
Si falta `uv`, se descarga el instalador del proveedor oficial Astral a un archivo temporal
y se ejecuta para instalar `uv` dentro de la carpeta de la aplicación. No se modifica su PATH.
Python 3.12 y las dependencias se resuelven con `uv.lock`; PyTorch usa la distribución CPU.

El proceso realiza estas operaciones y solo informa éxito después de comprobar el servicio:

1. Prepara la aplicación en `~/.local/share/odoo19-mcp-rag` (o `$XDG_DATA_HOME/odoo19-mcp-rag`).
2. Crea su entorno Python aislado, descarga las fuentes oficiales 19.0 y el modelo e indexa la documentación.
3. Genera configuraciones para los clientes dentro de `client-config/`.
4. Crea `~/.config/systemd/user/odoo19-docs.service` (respeta `$XDG_CONFIG_HOME`).
5. Activa `loginctl enable-linger` para que el servicio del usuario arranque con el equipo y
   continúe después de cerrar sesión. El sistema puede pedir autorización para esta operación.
6. Habilita e inicia el servicio y espera que `/readyz` responda.
7. Abre una sesión MCP HTTP, descubre ambas herramientas y ejecuta una consulta con una fuente oficial.

La primera instalación requiere Internet y varios minutos para indexar. Reserve al menos 4 GB de
disco; el consumo depende de las versiones y las cachés. Las búsquedas posteriores son locales.
Si un paso falla, el instalador termina con código distinto de cero y deja el estado `installing`.
Corrija el error y repita la misma orden; una instalación incompleta no se anuncia como lista.

## Ubicación del programa y del RAG

Con las opciones predeterminadas, el árbol instalado es:

```text
~/.local/share/odoo19-mcp-rag/
├── .venv/          # Entorno de ejecución
├── src/            # Servidor y recuperación
├── client-config/  # Ejemplos de conexión generados
└── data/
    ├── raw/        # Fuentes oficiales descargadas
    ├── chroma_db/  # Índice vectorial, búsqueda textual y manifiesto
    └── models/     # Modelo local y caché del tokenizador
```

Si se usa `$XDG_DATA_HOME` o `--prefix`, la misma estructura queda bajo ese destino.
El directorio desde el que se descargó o clonó el proyecto es una copia del código fuente;
la instalación normal no lo utiliza para servir consultas. La ruta efectiva de datos queda
registrada en `.odoo19-install.json` y en `ODOO_RAG_DATA_DIR` de la unidad systemd.

`--use-existing-data /ruta/al/data` **no copia los datos**: configura el servicio para utilizar
directamente esa carpeta. Si apunta al árbol del proyecto, este debe conservarse. Para una
instalación independiente, omita esa opción o traslade los datos como se indica más abajo.

## Conectar el cliente al servicio

```text
Transporte: Streamable HTTP
URL: http://127.0.0.1:8019/mcp
Autenticación del servidor: ninguna (solo loopback)
```

El endpoint es de protocolo MCP y no es una página web con buscador. Agregue la URL en el
cliente MCP de escritorio que utilice ejecución local. Las conversaciones ejecutadas en la nube
requieren un túnel o una integración remota adicional; este instalador no publica el servicio.

Para un cliente que use el formato JSON `mcpServers`:

```json
{"mcpServers": {"odoo19-docs": {"url": "http://127.0.0.1:8019/mcp"}}}
```

Para la configuración TOML local de Codex:

```toml
[mcp_servers.odoo19-docs]
url = "http://127.0.0.1:8019/mcp"
```

Los archivos `client-config/mcp-http.json` y `client-config/codex-http.toml` ya contienen el
puerto seleccionado. También se genera una alternativa `mcp-stdio.json`. El instalador no
modifica silenciosamente configuraciones existentes de ChatGPT, Claude o Cursor.
Use la conexión HTTP al servicio para compartir un único modelo cargado entre clientes.
Mantenga un solo proceso propietario de la base; cierre clientes stdio antes de actualizar datos.

Al pasar de una conexión stdio a este servicio, sustituya el registro del cliente por una
conexión Streamable HTTP con la URL anterior. No conserve `command`, `args` ni `cwd` en la misma
entrada HTTP. Deje vacíos el token y los headers de autenticación para este servicio de loopback.
El cliente deja de iniciar Python; systemd administra el proceso y su directorio de datos.
La URL no cambia cuando se trasladan los archivos dentro del mismo equipo.

## Operación diaria

Desde la carpeta fuente o instalada:

```bash
bash odoo19ctl status
bash odoo19ctl check
bash odoo19ctl logs
bash odoo19ctl stop
bash odoo19ctl start
bash odoo19ctl restart
# Descarga actualizaciones oficiales e indexa cambios, deteniendo y reiniciando el servicio:
bash odoo19ctl update
```

Si la actualización falla, el servicio permanece detenido para no servir un índice parcial.
Reanude la ingesta y luego arranque el servicio. Antes de cambiar fuentes, cierre también
cualquier cliente que siga utilizando stdio contra la misma base.

Para comprobar manualmente el transporte:

```bash
~/.local/share/odoo19-mcp-rag/.venv/bin/python \
  ~/.local/share/odoo19-mcp-rag/src/probe.py
```

Desde la carpeta instalada puede usar `./.venv/bin/python -m src.probe`.
`/healthz` comprueba que el proceso responde; `/readyz` comprueba que el índice está disponible.
El arranque del servicio también carga el modelo y verifica la integridad del índice.

## Opciones del instalador

```bash
bash install.sh --help
bash install.sh --prefix "$HOME/.local/share/odoo19-mcp-rag" --port 8020
# Reusar una instalación previa del índice y modelo, sin descargarlos de nuevo:
bash install.sh --use-existing-data /ruta/absoluta/al/data
# Arrancar al iniciar sesión, en lugar de habilitar persistencia desde el boot:
bash install.sh --no-linger
# Preparar archivos, entorno e índice sin registrar/iniciar un servicio:
bash install.sh --no-service
```

El destino debe estar vacío o tener el marcador de una instalación anterior de este proyecto.
Una reinstalación conserva `data/` y vuelve a validar el servicio. No borra directorios ajenos.
Una actualización del código instala el nuevo paquete desde su carpeta fuente ejecutando
`install.sh` con el mismo destino y las mismas opciones de datos/puerto.

## Trasladar datos externos a la instalación

Este procedimiento sirve cuando se instaló con `--use-existing-data` y se desea dejar el RAG
dentro de la carpeta instalada. Conserva el origen y no reconstruye los embeddings.
Antes de copiar, cierre los clientes stdio y cualquier ingesta que use la misma base.

Desde la carpeta fuente completa, ajuste `previous_data` a la carpeta actual y `installed_root`
al destino de instalación. El ejemplo usa el prefijo y el puerto predeterminados; si se
personalizaron, mantenga el mismo prefijo y añada `--port` con el puerto actual al reinstalar.
El destino `data/` debe estar ausente. Si ya existe, revise su contenido antes de continuar;
no mezcle dos índices ni sobrescriba una copia previa.

```bash
(
    set -e
    previous_data="/ruta/absoluta/al/data-actual"
    installed_root="$HOME/.local/share/odoo19-mcp-rag"
    test -f "$previous_data/chroma_db/manifest.json"
    test ! -e "$installed_root/data"
    bash odoo19ctl --prefix "$installed_root" stop
    cp -a -- "$previous_data" "$installed_root/data"
    bash install.sh --prefix "$installed_root" --use-existing-data "$installed_root/data"
    bash odoo19ctl --prefix "$installed_root" check
)
```

La reinstalación valida la base copiada, regenera las configuraciones y actualiza la unidad
systemd con la nueva ruta. Solo informa éxito después de una consulta MCP con fuente oficial.
Si se había elegido arranque al iniciar sesión, mantenga también `--no-linger` al reinstalar.
Revise con `systemctl --user cat odoo19-docs.service` que `ODOO_RAG_DATA_DIR` apunte al nuevo
destino. Conserve el origen hasta terminar estas comprobaciones.

Si la copia o validación falla, el servicio puede quedar detenido. No retire el origen: puede
reinstalar con el mismo prefijo y `--use-existing-data` apuntando a la carpeta anterior para
recuperar el servicio mientras revisa el error. Una copia incompleta no debe usarse como índice.

## Desinstalar el servicio

```bash
bash uninstall.sh
```

Deshabilita el arranque automático y retira la unidad systemd. Conserva el código, el modelo y
los datos para permitir recuperación. `linger` permanece habilitado porque otros servicios del
usuario podrían depender de él. Solo si sabe que no lo necesita para otros servicios puede
retirarlo con `loginctl disable-linger`.

## Alcance y seguridad

El servidor solo admite direcciones de loopback. No abre puertos en la interfaz de red, no
instala un proxy público y conserva la protección del SDK contra DNS rebinding. El servicio
se ejecuta sin privilegios elevados y se reinicia si falla. Esto no configura un acceso desde
ChatGPT web ni un despliegue multiusuario en una red corporativa.

Al publicar el proyecto se incluyen código, instalador, pruebas y archivo de versiones fijadas.
Se excluyen `.env`, datos, modelos, informes de la máquina, carpetas de instalación y claves.
La documentación descargada conserva la licencia y atribución de Odoo; el código propio usa MIT.
