Las opciones principales son:

TikTokLive (de isaackogan): Es la librería de código abierto más popular en Python para este propósito. Permite leer el chat y eventos de los streams mediante WebSockets. Al ser un proyecto basado en ingeniería inversa, a veces requiere actualizaciones frecuentes cuando TikTok cambia sus protocolos.  
GitHub

Instalación: pip install tiktok-live-api

Ejemplo básico de uso (TikTokLive)

```python

Python
from TikTokLive import TikTokLiveClient
from TikTokLive.types.events import CommentEvent, ConnectEvent

# Inicializar el cliente con el usuario del live (ej. @nombre_de_usuario)
client: TikTokLiveClient = TikTokLiveClient(unique_id="@nombre_de_usuario")

# Evento cuando se conecta exitosamente
@client.on("connect")
async def on_connect(event: ConnectEvent):
    print(f"Conectado al Live con Room ID: {client.room_id}")

# Evento cuando alguien comenta en el chat
@client.on("comment")
async def on_comment(event: CommentEvent):
    print(f"{event.user.nickname}: {event.comment}")

if __name__ == '__main__':
    # Ejecutar el cliente
    client.run()
```
Estas librerías son muy utilizadas para crear bots de chat interactivos, alertas de donaciones/regalos en pantalla (overlays para OBS) o herramientas de análisis de transmisiones.

La velocidad y el volumen de datos en un Live de TikTok son un desafío importante. Si un stream se vuelve viral, puedes recibir cientos de comentarios y regalos por segundo.

Si intentas que la librería TikTokLive envíe una petición HTTP individual a tu servidor FastAPI por cada evento (comentario, like, regalo), vas a saturar tanto el script de captura como el servidor, provocando latencia, cuellos de botella y pérdida de datos.

Para resolver esto, la arquitectura se diseña separando responsabilidades y usando patrones de desacoplamiento. Aquí te presento tres ideas arquitectónicas de cómo estructurar esto, de la más básica a la más profesional:

1. La Arquitectura de Agrupación (Batching) vía HTTP
Si estrictamente necesitas usar peticiones HTTP clásicas (POST), la regla de oro es nunca enviar un evento por petición. Se utiliza un patrón de búfer (buffer).

El Capturador (TikTokLive): Funciona como un recolector. Cada vez que llega un evento, no lo envía a FastAPI de inmediato, sino que lo guarda en una lista temporal en memoria.

El Temporizador: Cada "X" segundos (por ejemplo, cada 1 o 2 segundos) o cuando la lista alcanza un límite (ej. 100 comentarios), el script toma toda esa lista y hace una sola petición HTTP POST masiva a FastAPI.

FastAPI: Recibe el paquete grande, lo procesa rápidamente, lo guarda en la base de datos y responde con un "200 OK".

Ventaja: Reduce drásticamente la carga de red (el overhead de abrir y cerrar conexiones HTTP).

2. La Arquitectura de Puente WebSocket (Ideal para Tiempo Real)
FastAPI tiene un soporte nativo excelente para WebSockets. En lugar de hacer peticiones HTTP tradicionales (que abren y cierran la conexión cada vez), creas un túnel permanente.

Conexión Continua: El script de TikTokLive se conecta a TikTok por un lado (para leer los eventos) y abre una conexión WebSocket con tu servidor FastAPI por el otro.

Flujo de Datos (Streaming): A medida que los eventos entran desde TikTok, el script los inyecta inmediatamente al túnel WebSocket hacia FastAPI.

FastAPI: Escucha este túnel de forma asíncrona. Como no hay que negociar cabeceras HTTP nuevas por cada mensaje, el flujo es instantáneo y consume muy pocos recursos.

Aplicación: Esto es lo que se usa si tu FastAPI sirve de backend para mostrar alertas en pantalla (OBS) o un dashboard en tiempo real.

3. La Arquitectura de Productor-Consumidor con Colas (Grado Producción)
Esta es la arquitectura estándar en la industria para manejar ráfagas masivas de información impredecibles. Introduce un intermediario, generalmente Redis, RabbitMQ o Kafka.

Productor (TikTokLive): Su único trabajo es escuchar a TikTok y "escupir" los datos crudos a una cola en memoria (como Redis Pub/Sub o Streams). Es extremadamente rápido porque no espera respuesta de nadie.

El Intermediario (Message Broker): Retiene temporalmente la avalancha de eventos. Si llegan 5.000 likes en un segundo, los enfila ordenadamente.

Consumidor (FastAPI): Tu aplicación FastAPI (a menudo utilizando tareas en segundo plano como Celery o FastStream) va sacando los mensajes de la cola a su propio ritmo. Si FastAPI está ocupado escribiendo en la base de datos, no importa, porque Redis guarda los mensajes hasta que FastAPI esté listo para el siguiente.

Ventaja: Tolerancia a fallos. Si tu servidor FastAPI se reinicia o se cuelga por un segundo, no pierdes los eventos de TikTok porque están seguros en la cola.

