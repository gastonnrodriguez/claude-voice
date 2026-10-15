# claude-voice

Claude Code te avisa **por voz** cuando termina una tarea o necesita tu atención, para que puedas dejarlo trabajando en otro monitor sin estar mirándolo.

- "Gastón, terminé en tienda online." y enseguida un resumen de una línea que escribe el propio Claude ("listo el endpoint de pagos, falta probar el webhook").
- "Gastón, en app del clima necesito tu atención." cuando pide un permiso o te hace una pregunta.
- La respuesta de texto queda intacta: el resumen viaja en un comentario HTML (`<!-- voice: ... -->`) al final de la respuesta.
- Voz local y gratis con [Piper](https://github.com/OHF-Voice/piper1-gpl): sin API keys, sin GPU.
- Si varios agentes terminan a la vez, hablan de a uno.

Funciona en **Linux, macOS y Windows**, y habla **español, inglés, portugués, francés, alemán e italiano**.

## Instalación

Dentro de Claude Code:

```
/plugin marketplace add gastonnrodriguez/claude-voice
/plugin install voice@claude-voice
```

La primera sesión instala el motor de voz en segundo plano (~300 MB, una sola vez) y avisa "ya tengo voz" cuando termina. Mientras tanto usa la voz del sistema.

Requisitos: **Python 3.9 o superior**. El resto depende del sistema:

| Sistema | Qué hace falta | Voz mientras se instala |
|---|---|---|
| Linux | PipeWire o PulseAudio (lo normal en cualquier escritorio). No hace falta `python3-venv`: si falta, el plugin instala pip por su cuenta. | `spd-say` |
| macOS | Nada más: si no tenés Python, `python3` te ofrece instalarlo la primera vez. | `say` |
| Windows | Python desde [python.org](https://www.python.org/downloads/) o `winget install Python.Python.3.12`. Claude Code tiene que usar Git Bash para los hooks (lo normal si instalaste Git for Windows). | la voz de Windows |

## Configuración

Al instalar, el plugin te pregunta tres cosas. Para cambiarlas después: desde `/plugin`, en las opciones de **voice**.

- **Cómo querés que te llame.** Vacío usa el primer nombre de tu usuario del sistema (en Windows no dice nombre); con un guion (`-`) no dice ningún nombre.
- **Idioma:** `es`, `en`, `pt`, `fr`, `de` o `it`. Las frases, el resumen de Claude y la voz siguen ese idioma. Vacío usa el idioma del sistema (o inglés si no es uno de esos).
- **Voz:** cualquiera del [catálogo de Piper](https://huggingface.co/rhasspy/piper-voices). Vacío usa la voz por defecto del idioma (en español, `es_AR-daniela-high`).

Opcional, un `config.json`:

- Linux: `~/.config/claude-voice/config.json`
- macOS: `~/Library/Application Support/claude-voice/config.json`
- Windows: `%APPDATA%\claude-voice\config.json`



```json
{ "idioma": "es", "voz": "es_AR-daniela-high", "nombre": "Gastón" }
```

- Estas tres opciones también se pueden poner acá (`nombre`, `idioma`, `voz`); lo que elijas en el plugin tiene prioridad.

## Cómo funciona

Tres hooks de Claude Code:

| Evento | Qué hace |
|---|---|
| `SessionStart` | Le pide a Claude que termine sus respuestas con `<!-- voice: resumen -->` e instala el motor si falta. |
| `Stop` | Dice que terminó, el nombre del proyecto y el resumen. |
| `Notification` | Avisa cuando Claude espera tu aprobación o una respuesta. |

El hook responde en milisegundos y todo lo lento (generar y reproducir la voz) corre en segundo plano. Las frases fijas quedan en caché en la carpeta de datos del plugin, y ahí mismo está `claude-voice.log` por si algo no suena:

- Linux: `~/.local/share/claude-voice`
- macOS: `~/Library/Application Support/claude-voice`
- Windows: `%LOCALAPPDATA%\claude-voice`

No funciona en sesiones en la nube (el audio sonaría en el servidor) ni dentro de WSL.
