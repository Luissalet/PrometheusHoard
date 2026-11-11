# Prometheus's Hoard

**Modelos → En disco → Copiar a** admite una carpeta padre de destino opcional
mediante `model_copy(dest)` existente. Vacío conserva el destino habitual nativo;
un valor no vacío se envía literal para todas las Sparks elegidas. El modelo
conserva su nombre. Sigue aplicándose la adaptación nativa del directorio personal
y pueden actualizarse archivos coincidentes. Los cambios posteriores quedan sin
enviar tras una solicitud aceptada. Sin nuevo motor, carga, tool o historial de destino.

En **Modelos → Trabajos**, Spark y estado consultan `jobs_list` antes de su
límite de 50: permite encontrar un fallo antiguo fuera de los 50 últimos globales.
El conteo indica registros devueltos, no todo el historial. **Terminados**
conserva la vista local de los no-running entre esos registros y explica su
alcance. Detalle/registro, Mostrar más/menos y cancelación explícita permanecen.
El polling comparte una consulta idéntica pendiente; una respuesta de filtros
anteriores no sustituye el resultado actual. No añade creación/reintento de
trabajos, sondeos remotos, backend ni herramientas a MCP45.

En **Modelos → Descargar de Hugging Face**, **Sólo estos archivos** admite patrones de nombre opcionales, uno por línea, mediante `model_download(include)` API/MCP existente. Vacío mantiene la descarga del repositorio completo. Conserva comas, comillas y espacios dentro del patrón; no lista archivos remotos ni filtra en frontend. El tamaño esperado y el porcentaje usan la estimación nativa del repositorio entero, no una estimación filtrada. Elegir patrones no acredita que estén todos los archivos necesarios para cargar un modelo. Un error conserva el borrador; las ediciones realizadas durante el envío siguen sin enviar tras aceptar el trabajo. Trabajos permite seguirlo o cancelarlo como antes. No se añaden patrones al historial/metadatos guardados de trabajos.

En **Equipo → Red CX7 · Pares de Sparks**, consulta y copia las direcciones
destino detectadas de cada par mediante `fabric_status`. El detalle muestra el
estado y velocidad declarados, MTU e IPv4 de las interfaces. Lee métricas en caché
manualmente: no sondea ni prueba conexión o rendimiento. El motor devuelve la
primera dirección IPv4 /24 coincidente entre interfaces CX7 activas; un valor
ausente no demuestra un cable averiado. La hora de consulta no fecha las métricas.

En **Modelos → Trabajos**, **Ver detalle** consulta el trabajo seleccionado con
`job_get`, sin iniciar, reintentar ni cancelar su ejecución. Muestra identificador,
estado, código de salida, fechas registradas de creación y finalización, progreso
y bytes cuando constan, y las últimas hasta 40 líneas disponibles del registro.
La creación no indica inicio de ejecución; los datos ausentes se señalan.
**Actualizar** vuelve a leer ese detalle. Se conservan el resumen de seis líneas
de la lista y sus controles de cancelación.

Maneja desde el PC con Windows un clúster pequeño de NVIDIA DGX Spark. Cada Spark es una unidad en un explorador de archivos; su GPU, CPU, memoria unificada, consumo, discos y red se ven en directo; los modelos de sus discos se listan y se descargan; los modelos se cargan y se descargan de memoria con recetas; y cada Spark se puede bloquear, suspender, apagar, reiniciar o encender desde la misma ventana. Es de la familia Hoard: funciona sola, desde Hoard Hub, desde Faustus y desde cualquier cliente MCP.

![Equipo](docs/screens/equipo.png)

## Qué hace

- **Equipo**: una tarjeta por Spark con estado, tiempo encendida, uso de GPU, temperatura, consumo y reloj, uso de CPU por núcleo, memoria unificada, disco, enlaces CX7 y los modelos que sirve (cabeza o trabajador). Vista tipo Administrador de tareas por Spark con gráficas grandes, procesos, contenedores, servidores de inferencia detectados e interfaces de red.
- **Sirviendo**: cifras en vivo de cada servidor de inferencia en marcha (de receta o arrancado a mano), leídas de la página de Prometheus que publica vLLM en `/metrics`: tokens por segundo de generación y de lectura del prompt con una gráfica de diez minutos, peticiones en curso y en cola, uso de la caché KV, tokens por paso y aceptación de la decodificación especulativa si el servidor tiene borrador, mediana y percentil 95 del tiempo hasta el primer token y del tiempo entre tokens (también como tokens por segundo por flujo), un aviso si hubo peticiones expulsadas y los tokens servidos hasta ahora, con la URL base lista para copiar. La página se actualiza cada 2 s mientras está abierta. Cada tarjeta termina con **Clientes**: quién usa ese servidor, leído del registro de accesos HTTP de su contenedor (vLLM no apunta quién pregunta, pero su servidor web registra cada petición): por dirección de cliente, las peticiones por API (OpenAI chat, completions, Responses, Anthropic Messages, embeddings, otras), peticiones por hora, errores (que no sean 2xx), cuándo se vio por última vez y una etiqueta si solo sondea `/health`, `/v1/models` o `/metrics`. Los nombres se editan ahí mismo (ajuste `client_names`, herramienta `client_name_set`); este PC se nombra solo, las Sparks por sus propias direcciones, y al desplegar **Este PC** salen los programas de aquí que tienen conexiones abiertas con el servidor ahora mismo (las apps Hoard por su módulo, Faustus, asistentes y, si no, el ejecutable). Las sondas del propio servidor desde 127.0.0.1 solo se cuentan.
- **Tarjetas de velocidad y de capacidad** (en cada servidor de Sirviendo, separadas a propósito): **Velocidad** es una medición que se lanza con **Medir** (pide confirmación: manda cientos de peticiones de chat en streaming al servidor compartido durante varios minutos). Corre como trabajo con 1, 8, 16 y 64 flujos a la vez, un prompt único por petición (un nonce aleatorio al principio, así la caché de prefijos nunca responde), una ronda de calentamiento descartada y la mediana de tres rondas, y da por nivel los tokens por segundo agregados, los tokens por segundo de decodificación de un flujo (mediana), el tiempo hasta el primer token (p50 y p95) y las peticiones fallidas. Cuenta todos los tokens generados, razonamiento incluido (el `usage` del servidor si lo envía y, si no, los fragmentos del stream). Los resultados se guardan en `data/speedcards.json` (los últimos 20 por servidor). **Capacidad** se lee en vivo y cuesta dos peticiones pequeñas: el contexto que acepta el servidor (`/v1/models`), el pool KV en tokens (`vllm:cache_config_info`: bloques × tamaño de bloque, con cuántas peticiones de contexto completo caben) y lo que la receta registró como verificado. Herramientas: `speed_card` (lectura) y `speed_card_run` (lanza el trabajo; exige `confirm=true`; quien llama debe tener el bloqueo del modelo principal; una medición a la vez por servidor; se cancela con `job_cancel`).
- **Errores de GPU**: cada 60 s (`xid_interval_s`; `xid_watch` lo apaga en Ajustes) lee los mensajes nuevos del kernel de cada Spark (`journalctl -k --since ... -o short-iso` por SSH) y guarda las líneas que informan de un `Xid` de NVIDIA (con su código), un reinicio completo del chip (`NV_ERR_GPU_IN_FULLCHIP_RESET`) o un error, tiempo de espera o fallo de GSP, en `data/xid_events.json` (las últimas 500). Salen arriba en Sirviendo, marcadas en la tarjeta de la Spark y en la herramienta `xid_events`, y cada una nueva se avisa por el canal de notificaciones del hub de la familia (`xid_notify`; el hub decide cómo). Solo avisa: nunca reinicia ni resetea nada.
- **Escritorio (escritorio remoto)**: desde la página de una Spark, **Escritorio** muestra y maneja su propio escritorio gráfico en el navegador, como un programa de escritorio remoto pero integrado. Bajo demanda, la Spark arranca un servidor TigerVNC con una sesión XFCE en una pantalla virtual (no hace falta monitor ni gestor de sesión); solo existe mientras alguien mira. La primera vez por Spark: **Instalar escritorio remoto** (pide confirmación; `sudo -n apt-get install` de TigerVNC y XFCE como un trabajo que se sigue en Modelos/Trabajos). Después, **Iniciar escritorio**, y la página conecta con un cliente noVNC incluido: conectar/desconectar, pantalla completa, Ctrl+Alt+Supr y el estado de la conexión; también funciona con ancho de móvil (ver y táctil). **Ajustar resolución** (por defecto con ventana ancha) cambia la resolución de la pantalla de la Spark al tamaño de la ventana del navegador y la sigue al redimensionarla o en pantalla completa; **Escalar** mantiene la resolución remota y encoge o estira la imagen; **Tamaño real** la muestra píxel a píxel con barras de desplazamiento. La barra enseña la resolución remota actual. Portapapeles en los dos sentidos y automático (se puede desactivar): el texto copiado en la Spark pasa al portapapeles de este PC al momento; Ctrl+V, Ctrl+Mayús+V o Mayús+Insert dentro de la pantalla pone primero el portapapeles de este PC (texto o imagen) en el de la Spark y luego pega; una imagen copiada en la Spark llega a este PC al sacar el ratón de la pantalla o con **Traer portapapeles del escritorio**, con vista previa y **Guardar imagen**; **Enviar imagen…** manda un archivo de imagen. Los archivos que se arrastran sobre la pantalla se suben a la carpeta Escritorio de la Spark. Ajustes (Ajustes > Escritorio remoto): `desktop_display` (por defecto `:5`, puerto VNC 5905; cada pantalla guarda sus ficheros en `~/.config/prometheus-desktop/d<n>`), `desktop_geometry` (1600x900, el tamaño con el que arranca), `desktop_idle_min` (30; un vigilante en la Spark detiene la sesión tras esos minutos sin nadie mirando, aunque la app esté cerrada; 0 = nunca) y `desktop_keymap` (es). Herramientas: `desktop_status`, `desktop_install` (`confirm`), `desktop_start`, `desktop_stop`, `desktop_clipboard_get` (texto, o el tamaño de una imagen; el PNG con `include_image`) y `desktop_clipboard_set` (texto). Rutas: `GET /api/desktop/{node}/clipboard`, `POST /api/desktop/{node}/clipboard/image` (cuerpo PNG).
- **Historial**: cada muestra del sondeo se guarda también por minuto en `data/history.sqlite3` durante `history_days` días (90 por defecto): uso de GPU (media y pico), CPU, memoria unificada, temperatura de GPU (media y pico) y consumo (medio y pico). La página enseña 1 h, 6 h, 24 h, 7 días o 30 días de todas las Sparks o de una, una línea por Spark con un cursor que lee todos los valores, huecos donde no hubo datos y las caídas sombreadas, y un resumen por Spark (minutos con datos, GPU media y pico, temperatura máxima, consumo, energía en kWh y, con `energy_price_kwh`, el coste). Herramienta `metrics_history`, ruta `GET /api/history?node=&hours=`.
- **Avisos**: una Spark cuyo sondeo sigue fallando `alert_after_s` segundos (90), o un servidor de modelos que la app tiene como cargado (o que vio contestar) y cuya comprobación de salud sigue fallando, abre una incidencia: aviso rojo en todas las páginas y contador en Historial, la lista de caídas con inicio y duración, un evento en el bus de la familia (`prometheus.alert.spark_down` / `spark_up` / `server_down` / `server_up`) y, con `alert_notify`, una notificación por el hub. Apagar una Spark desde la app queda como prevista y no se notifica; descargar una receta no es una incidencia. Herramienta `alerts`, ruta `GET /api/alerts`; ajustes `alert_watch`, `alert_after_s`, `alert_notify`, `history_days`, `energy_price_kwh`. Una Spark que deja de responder sin que la app la haya apagado sale como «sin respuesta», no como apagada.
- **Archivos**: explorador con cada Spark como unidad. Barra de direcciones, atrás/adelante/arriba, búsqueda, vista de detalles e iconos, ocultos, vista previa (texto con números de línea, JSON, imágenes, vídeo, audio) y edición de texto, subir arrastrando, descargar, nueva carpeta y archivo, renombrar (F2), cortar/copiar/pegar (Ctrl+X/C/V), enviar a otras Sparks por los cables CX7, propiedades con el tamaño de la carpeta y papelera por Spark con restaurar. Solo escribe dentro de la carpeta personal del usuario de la Spark; el resto del disco se puede mostrar en solo lectura.
- **Modelos**: lo que está cargado con su URL compatible con OpenAI; recetas con Cargar, Descargar de memoria y Reiniciar modelo (si otra receta usa esas Sparks lo dice y ofrece descargarla antes); modelos en cada disco con tamaño, arquitectura, cuantización y contexto; descargas de Hugging Face; copias entre Sparks por CX7 (rsync, reanudables); trabajos con progreso y registros. Reiniciar detiene todos los nodos en orden inverso al arranque antes de cargar la misma receta; un fallo al detener queda visible y permite reintentar, sin iniciar otra copia.
- **Encendido**: bloquear, suspender, apagar y reiniciar una Spark o todas; encender usa wake-on-LAN con la MAC del cable que aprendió mientras estaba encendida. Antes de apagar descarga los modelos de esa Spark.
- **Endpoints para otros programas**: `GET /api/endpoints` da los servidores de inferencia en marcha (primero la receta por defecto). Faustus lo lee para usar las Sparks como backend por defecto. `GET /api/serving` devuelve las cifras en vivo de esos servidores (`{endpoints, totals, poll_s, now}`; `?recipe=` filtra, `?series=false` quita los puntos de las gráficas) y la herramienta `serving_stats` da lo mismo a un asistente (cada endpoint lleva `clients`, salvo con `?clients=false` / `clients: false`). `sparks_overview` lleva un resumen corto `serving` por endpoint.

![Sirviendo](docs/screens/sirviendo.png)

![Archivos](docs/screens/archivos.png)

![Modelos](docs/screens/modelos.png)

## Comparar mediciones guardadas

En Sirviendo, **Comparar mediciones** abre los últimos 20 registros de velocidad de ese servidor. Elige base, otra medición y concurrencia: cada fila indica sus flujos y muestra velocidad agregada/por flujo, primer token p50/p95, peticiones fallidas o medidas, con cambio absoluto y porcentual. **Intercambiar** invierte la comparación. Un dato ausente sigue sin dato; con base cero hay cambio absoluto pero no porcentaje.

Modelo, límite de salida, rondas, calentamiento, fin de secuencia y origen del recuento de tokens se muestran a ambos lados, señalando diferencias. Solo lee registros; no lanza un benchmark. Compara resultados, no causas: otros clientes o condiciones no registradas pueden haber cambiado.

## Recetas

Las recetas medidas muestran los tokens de entrada, la velocidad de generación estimada, el tiempo hasta el primer token y el resultado de recuperación. «Ver todas las mediciones» despliega todos los valores registrados, incluidas las pruebas de referencia anidadas, las posiciones de recuperación y la memoria de caché. Los datos proceden de `measured` en la receta; el panel no ejecuta pruebas ni certifica sus resultados.

Una receta es una carpeta con `recipe.json` y los scripts que arrancan y paran un servidor (`start.sh`, `stop.sh`, y si quiere `health.sh` y `logs.sh`). La carpeta de recetas es un ajuste (por defecto `Sparks cluster/recipes`, junto a este repositorio). Cargar copia la carpeta a cada Spark que usa, ejecuta `start.sh` en cada una (primero la cabeza salvo que `start_order` diga otra cosa) como trabajo en segundo plano y espera a que la cabeza responda en su puerto. Una receta con `"coexist": true` es un servidor pequeño que corre junto al modelo principal en las mismas Sparks (embeddings, reranking o un modelo de decisiones): no es conflicto para las otras recetas ni ellas para él; solo se comprueba su `memory_gb` contra la memoria libre. `"kind"` (`chat` por defecto, o `decision`, `embedding`, `rerank`) viaja con su endpoint para que los clientes solo ofrezcan como modelo de chat los de tipo chat.

## Arrancar

```
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python -m prometheus_hoard        # http://127.0.0.1:5205
```

Las Sparks se alcanzan con la configuración SSH del PC (`~/.ssh/config` y sus `Include`; NVIDIA Sync escribe ahí los alias `Spark1`..`Spark3`). En Ajustes cada Spark puede tener otra dirección o un salto intermedio. `PROMETHEUS_FAKE=1` arranca un clúster inventado de tres Sparks para probar la interfaz sin hardware.

El puente MCP es `python mcp_server.py` (53 herramientas). `deploy_restart` recarga una receta en segundo plano, o espera con `wait=true`; el progreso se consulta con `deployments` y `deploy_logs`. Los reinicios duplicados se rechazan hasta terminar la recarga. Conserva la receta y la configuración del endpoint; reiniciar no certifica la calidad de las respuestas del modelo. Las destructivas (borrado permanente, vaciar la papelera, borrar un modelo, encendido, comandos) piden `confirm=true`.

## Qué no hace

- Las cifras de Sirviendo salen solo del `/metrics` de vLLM: un servidor de otro motor aparece en la lista con el motivo por el que no tiene cifras. Se muestrea cada 2 s mientras la página o un asistente miran y cada 10 s en el resto del tiempo; se guardan unos 15 minutos de muestras en memoria, así que las gráficas empiezan vacías tras reiniciar la app.
- Los clientes salen del registro de accesos del contenedor, leído en la Spark principal con `docker logs --since` (`sudo -n docker` si el usuario no está en el grupo docker; si no va ninguno, la tarjeta lo dice) cada ~20 s mientras la página o un asistente miran y cada 2 min en el resto; un resumen de 24 h queda en `data/clients.json`. Una dirección es un equipo: los programas de otros ordenadores no se distinguen, un cliente tras un proxy sale como el proxy y el registro solo guarda lo que el contenedor ha escrito desde que arrancó. Solo en este PC se distinguen programas, con la tabla de conexiones del sistema operativo (`psutil`): un programa de otro usuario puede salir sin nombre, y la lista es una foto de lo conectado ahora, no un historial.
- Los totales de tokens servidos (`data/serving.json`, se guarda cada 30 s y al salir) solo cuentan lo que la app vio mientras estaba en marcha. Los contadores de los servidores se reinician con su contenedor; la app detecta la bajada y suma el valor anterior como desplazamiento, pero lo servido con la app cerrada (y un servidor que se reinició en ese hueco) se pierde.
- La tarjeta de velocidad mide el servidor tal como está cuando se pide: compite con lo demás que use el modelo, un servidor que rechace `ignore_eos` puede cortar algún flujo antes, y solo vale para servidores que hablen el chat en streaming de OpenAI. El pool KV es el que vLLM informa por worker; no promete que una petición de esa longitud se sirva. De una medición cancelada no se guarda nada.
- El vigilante de errores de GPU solo ve lo que el usuario de la Spark puede leer del journal del kernel (`journalctl -k` sin sudo), consulta cada cierto tiempo en vez de seguir el registro y solo avisa si el Hoard Hub responde; sin hub el evento se guarda y se muestra igualmente. El cursor de cada lectura sigue el reloj de la Spark.
- El escritorio remoto es una sesión VNC propia, no la consola de la Spark: no hay 3D con aceleración de GPU ni audio, y lo que haya abierto se cierra cuando se detiene. Instalarlo necesita `sudo` sin contraseña para `apt-get` en esa Spark (si no, la página lo dice y muestra los paquetes para instalar a mano). El puerto VNC solo escucha en el loopback de la Spark y se alcanza por la conexión SSH de la app, así que es tan privado como esa conexión; la contraseña de sesión de cada Spark (8 caracteres, el límite de VNC) queda en `data/desktop-passwords.json` y la lee la página de la app para conectar; los asistentes nunca la reciben. El WebSocket solo acepta el origen de la propia página. El portapapeles lleva texto e imágenes (hasta 25 MB), no archivos: suéltalos sobre la pantalla o usa Archivos. Las imágenes necesitan GTK para Python en la Spark (`python3-gi`, ya presente en DGX OS); leer el portapapeles de este PC pide permiso al navegador una vez. El historial solo cuenta lo que la app vio mientras estaba en marcha y la energía es la que informa la GPU, no la del enchufe.
- No instala drivers, no cambia la red de las Sparks ni compila motores de inferencia: eso lo hacen las recetas.
- Apagar, reiniciar y suspender necesitan `sudo` sin contraseña (o una regla de polkit) en las Sparks; si no, lo rechaza y dice la línea que hay que añadir.
- El wake-on-LAN solo funciona si el firmware de la Spark lo tiene activado.
- No descarga carpetas enteras al PC de una vez (se envían a otra Spark o se comprimen antes).

## Asistente de Faustus

Con el lanzador compartido de Hoard Link instalado, la app muestra el panel de Faustus de la familia (un botón flotante). La conversación, el modelo y las herramientas viven en Faustus; esta app solo le dice al panel qué hay abierto.

- **Contexto por vista.** El panel recibe el Spark abierto en su página como `spark` (conectado, estado de energía, activado, datos de host y SSH, error), hasta 8 despliegues activos como `deployment` (id de receta, estado, nodos, cabeza, URL base, nombre del modelo servido, motor, paso) y, en la página de ajustes, `settings` (carpetas de recetas y modelos, endpoint por defecto, sondeo). Un cliente que se está renombrando se nombra como `client`. La vista indica la página (`cluster`, `spark`, `serving`, `files`, `models`, `settings`). El token de acceso al repositorio de modelos nunca se envía.
- **Campos sin guardar.** El panel los pide con el evento de ventana `prometheus:context-request`. Los formularios `settings-form`, `model-download-new` (aún sin objeto), `client-name-form` y `file-editor` (solo la ruta del archivo y la longitud del borrador, no el texto) responden con una marca `dirty`.
- **Refresco.** Cuando el asistente termina un turno, la vista abierta se recarga en el sitio (`hoard:copilot-turn-end` / `window.hoardCopilot.onTurnEnd`). En ajustes, lo que no has tocado sigue a los valores nuevos, lo escrito se queda y los Sparks que añadiste también.
- **Guardado.** La página de ajustes envía solo los campos cambiados a `settings_set`. Argumentos nuevos: `nodes_patch` (`{id del Spark: campos a cambiar}`; un id renombra, un id desconocido añade un Spark y necesita `ssh`) y `nodes_remove` (ids a quitar), de modo que un Spark cambia sin reenviar la lista. El resto de ajustes se fusionan por clave.
- **Pruebas.** `node --test client/copilot-context.test.mjs` y `python -m pytest tests/test_settings_patch.py`.

## Recuperación de generación estructural

deploy_report_issue informa de degeneración tipada en una receta gestionada y devuelve un trabajo;
deploy_recovery_status consulta su estado. Exige dos fallos estructurales en canarios completos antes
de recargar con el lifecycle existente, lock del modelo principal y lease del Hub como Prometheus.
Espera métricas nuevas de inactividad, revalida identidad/configuración y prueba después de recargar.
Length, fences y errores lógicos aislados no disparan la recuperación. El manifiesto literal no verifica
el algoritmo; recovered sólo describe esos postcanarios. No se han validado discriminativamente contra
el estado malo conservado. [Alcance y límites](docs/recovery.md).
Los fallos de renovación quedan visibles; la supervisión sigue hasta estabilizar el lifecycle y exige
coordinación fresca antes de su worker de carga. Cancelar no abandona una descarga/carga activa.

Licencia MIT.

## Skills para los agentes que se conectan

Esta app lleva procedimientos de la biblioteca de skills del espacio de trabajo de la familia para los agentes que se conectan: `cluster-model-serving`, `deployment-patterns`, `docker-patterns`, `performance-optimization`. Aparecen en lo que el agente lee al conectarse, se leen con `skills_list` y `skill_read`, se buscan y asignan con `skills_library` y `skills_assign`, y se ofrecen como prompts. Se eligen en la página de skills (`/api/agent/skills/page`) o en la pestaña Skills del hub; el bloque `skills` de `faustus-plugin.json` guarda lo que la app pide por defecto.
