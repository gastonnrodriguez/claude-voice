# claude-voice

Claude Code te avisa **por voz** cuando termina una tarea o necesita tu atención, para que puedas dejarlo trabajando en otro monitor sin estar mirándolo.

- "Ana, terminé en tienda online." y enseguida un resumen de una línea que escribe el propio Claude ("listo el endpoint de pagos, falta probar el webhook").
- "Ana, en app del clima necesito tu atención." cuando pide un permiso o te hace una pregunta.
- La respuesta de texto queda intacta: el resumen viaja en un comentario HTML (`<!-- voice: ... -->`) al final de la respuesta.
- Voz local y gratis con [Piper](https://github.com/OHF-Voice/piper1-gpl): sin API keys, sin GPU.
- Si varios agentes terminan a la vez, hablan de a uno.

> MVP: por ahora solo Linux.

## Instalación

Dentro de Claude Code:

```
/plugin marketplace add gastonnrodriguez/claude-voice
/plugin install voice@claude-voice
```

La primera sesión instala el motor de voz en segundo plano (~300 MB, una sola vez) y avisa "ya tengo voz" cuando termina. Mientras tanto usa la voz del sistema (`spd-say`).

Requisitos: Python 3 y PipeWire o PulseAudio. No hace falta `python3-venv`: si falta, el plugin instala pip por su cuenta.

## Configuración

Al instalar, el plugin te pregunta **cómo querés que te llame**. Para cambiarlo después: desde `/plugin`, en las opciones de **voice**, o con `claude plugin configure voice@claude-voice`. Si lo dejás vacío usa el primer nombre de tu usuario del sistema; con un guion (`-`) no dice ningún nombre.

Opcional, en `~/.config/claude-voice/config.json`:

```json
{ "voz": "es_AR-daniela-high" }
```

- `voz`: cualquier voz del [catálogo de Piper](https://huggingface.co/rhasspy/piper-voices).
- `nombre`: también se puede poner acá; lo que elijas en el plugin tiene prioridad.

## Cómo funciona

Tres hooks de Claude Code:

| Evento | Qué hace |
|---|---|
| `SessionStart` | Le pide a Claude que termine sus respuestas con `<!-- voice: resumen -->` e instala el motor si falta. |
| `Stop` | Dice que terminó, el nombre del proyecto y el resumen. |
| `Notification` | Avisa cuando Claude espera tu aprobación o una respuesta. |

El hook responde en milisegundos y todo lo lento (generar y reproducir la voz) corre en segundo plano. Las frases fijas quedan en caché en `~/.local/share/claude-voice/cache`. Si algo no suena, mirá `~/.local/share/claude-voice/claude-voice.log`.

No funciona en sesiones en la nube (el audio sonaría en el servidor) ni dentro de WSL.
