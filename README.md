# Trivia para TikTok LIVE

Juego de preguntas por comentarios. La audiencia vota escribiendo `A`, `B`, `C` o
`D` en el chat de un directo; la partida se juega sola y OBS muestra el overlay.

## Requisitos

- Python 3.11+
- [`tiktoklives_back`](http://127.0.0.1:8081) corriendo (API de lectura de eventos)

## Instalación

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env   # opcional, los defaults funcionan
```

## Arranque

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- Consola del host: <http://127.0.0.1:8000/control>
- Overlay para OBS: <http://127.0.0.1:8000/overlay>
- Récord de puntajes: <http://127.0.0.1:8000/ranking>

El servidor escucha solo en loopback y no tiene autenticación: está pensado para
la misma máquina que hace de OBS.

En OBS, agregar una fuente **Navegador** apuntando a `http://127.0.0.1:8000/overlay`,
tamaño 1920x1080, y activar *Acceso a archivos locales* solo si fuera necesario.

## Cómo se juega

1. Crear la partida con el `room_id` del directo. **Crear e iniciar** también la
   arranca: a partir de ahí el servidor lleva el ritmo solo.
2. Cada ronda acepta votos durante 20 segundos. El overlay muestra la cuenta
   regresiva y el tally en vivo mientras la gente vota.
3. Al agotarse la ventana, el servidor cierra la ronda, puntúa y muestra la
   respuesta correcta durante 8 segundos.
4. Luego abre la siguiente. Al terminar la última, la partida queda `finished` y
   el overlay muestra el podio final.

El host puede tomar el control en cualquier momento: **Detener automático** deja
la ronda abierta tal cual está, y **Cerrar ahora** / **Revelar** /
**Abrir siguiente** hacen el mismo trabajo que antes. El runner decide qué falta
según lo que dice la base de datos, así que reanuda una partida interrumpida y
adopta una ronda que él mismo abrió a mano.

**El reloj vive en el servidor.** El overlay solo consulta por polling, y si el
tiempo corriera en el navegador el juego se detendría en cuanto el host cerrara
la pestaña. `GET /shows/{id}` manda `deadline_epoch_ms` junto con
`server_epoch_ms` para que el cliente calcule lo que falta sin depender de su
propio reloj.

## Reglas

**Voto único.** Una persona vota una sola vez por ronda. La restricción es única
en base de datos (`UNIQUE (round_id, user_id)`), no solo lógica de aplicación: en
un directo medido un usuario produjo el 46,5 % de los comentarios.

**Qué cuenta como voto.** Solo mensajes que son exactamente una letra A-D con
adorno no verbal opcional (`A`, ` a `, `A?`, `!a`, `🔥B`). En 200 comentarios
reales, el 100 % contenía alguna letra A-D, así que buscar subcadena contaría
"jacinta aceptas tarjeta" como tres votos.

**Puntos.** Solo aciertos puntúan. Base según dificultad (100/150/200) más un
bonus lineal de hasta el 50 % según el tiempo restante. Respuesta incorrecta = 0.

**Primer respondedor.** Se muestra el avatar de la primera persona que eligió
cada opción, acierte o no. Es visual, no puntúa.

**Ventana de ronda.** Cada ronda captura el `event_id` monotónico del upstream al
abrirse y lo congela al cerrarse. Solo se aceptan votos dentro de esa ventana, así
que el chat anterior no se cuela y los eventos tardíos no alteran un resultado ya
mostrado.

## Preguntas

`data/questions.json`, 15 preguntas. Cada una tiene exactamente 4 opciones y una
correcta. Un archivo inválido impide el arranque en lugar de cargarse a medias:
una pregunta con 3 opciones significaría letras desalineadas y una respuesta
incorrecta delante del público.

## Tests

```powershell
python -m pytest -q
node web/js/current_round_test.mjs
```

153 tests en Python: parser contra 200 comentarios reales, ciclo de ronda contra
SQLite real, paginación del upstream, cursor del poller, el runner de autoplay
(con ventanas colapsadas a milisegundos) y el contrato de la API.

`current_round_test.mjs` cubre el helper del frontend que decide qué ronda está en
pantalla. No hay runner de JS en el proyecto: el archivo se ejecuta con `node` y
sale con código distinto de cero si algo falla, así que sirve igual en CI.

## Estructura

```
app/
  domain/      reglas puras: parser, scoring, entidades de sesión
  infra/       ORM, cliente upstream, cache de avatares, poller, seed
  services/    ciclo de ronda: abrir, ingerir, congelar, revelar
               show_runner.py: una task por partida que lleva el ritmo solo
  api/         rutas y esquemas
web/           consola del host y overlay (sin build)
data/          preguntas
tests/
```