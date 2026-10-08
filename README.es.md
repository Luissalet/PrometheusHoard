# Prometheus's Hoard

Maneja desde el PC con Windows un clúster pequeño de NVIDIA DGX Spark. Cada Spark es una unidad en un explorador de archivos; su GPU, CPU, memoria unificada, consumo, discos y red se ven en directo; los modelos de sus discos se listan y se descargan; los modelos se cargan y se descargan de memoria con recetas; y cada Spark se puede bloquear, suspender, apagar, reiniciar o encender desde la misma ventana. Es de la familia Hoard: funciona sola, desde Hoard Hub, desde Faustus y desde cualquier cliente MCP.

![Equipo](docs/screens/equipo.png)

## Qué hace

- **Equipo**: una tarjeta por Spark con estado, tiempo encendida, uso de GPU, temperatura, consumo y reloj, uso de CPU por núcleo, memoria unificada, disco, enlaces CX7 y los modelos que sirve (cabeza o trabajador). Vista tipo Administrador de tareas por Spark con gráficas grandes, procesos, contenedores, servidores de inferencia detectados e interfaces de red.
- **Archivos**: explorador con cada Spark como unidad. Barra de direcciones, atrás/adelante/arriba, búsqueda, vista de detalles e iconos, ocultos, vista previa (texto con números de línea, JSON, imágenes, vídeo, audio) y edición de texto, subir arrastrando, descargar, nueva carpeta y archivo, renombrar (F2), cortar/copiar/pegar (Ctrl+X/C/V), enviar a otras Sparks por los cables CX7, propiedades con el tamaño de la carpeta y papelera por Spark con restaurar. Solo escribe dentro de la carpeta personal del usuario de la Spark; el resto del disco se puede mostrar en solo lectura.
- **Modelos**: lo que está cargado con su URL compatible con OpenAI; recetas con botón Cargar (si otra receta usa esas Sparks lo dice y ofrece descargarla antes); modelos en cada disco con tamaño, arquitectura, cuantización y contexto; descargas de Hugging Face; copias entre Sparks por CX7 (rsync, reanudables); trabajos con progreso y registros.
- **Encendido**: bloquear, suspender, apagar y reiniciar una Spark o todas; encender usa wake-on-LAN con la MAC del cable que aprendió mientras estaba encendida. Antes de apagar descarga los modelos de esa Spark.
- **Endpoints para otros programas**: `GET /api/endpoints` da los servidores de inferencia en marcha (primero la receta por defecto). Faustus lo lee para usar las Sparks como backend por defecto.

![Archivos](docs/screens/archivos.png)

![Modelos](docs/screens/modelos.png)

## Recetas

Una receta es una carpeta con `recipe.json` y los scripts que arrancan y paran un servidor (`start.sh`, `stop.sh`, y si quiere `health.sh` y `logs.sh`). La carpeta de recetas es un ajuste (por defecto `Sparks cluster/recipes`, junto a este repositorio). Cargar copia la carpeta a cada Spark que usa, ejecuta `start.sh` en cada una (primero la cabeza salvo que `start_order` diga otra cosa) como trabajo en segundo plano y espera a que la cabeza responda en su puerto.

## Arrancar

```
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python -m prometheus_hoard        # http://127.0.0.1:5205
```

Las Sparks se alcanzan con la configuración SSH del PC (`~/.ssh/config` y sus `Include`; NVIDIA Sync escribe ahí los alias `Spark1`..`Spark3`). En Ajustes cada Spark puede tener otra dirección o un salto intermedio. `PROMETHEUS_FAKE=1` arranca un clúster inventado de tres Sparks para probar la interfaz sin hardware.

El puente MCP es `python mcp_server.py` (40 herramientas). Las destructivas (borrado permanente, vaciar la papelera, borrar un modelo, encendido, comandos) piden `confirm=true`.

## Qué no hace

- No instala drivers, no cambia la red de las Sparks ni compila motores de inferencia: eso lo hacen las recetas.
- Apagar, reiniciar y suspender necesitan `sudo` sin contraseña (o una regla de polkit) en las Sparks; si no, lo rechaza y dice la línea que hay que añadir.
- El wake-on-LAN solo funciona si el firmware de la Spark lo tiene activado.
- No descarga carpetas enteras al PC de una vez (se envían a otra Spark o se comprimen antes).

Licencia MIT.
