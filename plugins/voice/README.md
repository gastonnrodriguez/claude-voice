# Voice: spoken notifications for Claude Code

Claude Code tells you **out loud** when it finishes a task or needs your attention, so you can leave it working on another monitor without watching it.

- When Claude finishes: "Gastón, terminé en tienda online." followed by a one-line summary that Claude writes itself ("listo el endpoint de pagos, falta probar el webhook").
- When Claude asks for a permission or a question: "Gastón, en app del clima necesito tu atención."
- Claude's reply stays untouched: the summary travels in an HTML comment (`<!-- voice: ... -->`) at the end of the reply, which markdown does not display.
- Local, free text-to-speech with [Piper](https://github.com/OHF-Voice/piper1-gpl): no API keys, no GPU, nothing sent to a speech service.
- If several sessions finish at once, they take turns.

Works on **Linux, macOS and Windows**, in Claude Code only (hooks do not run on claude.ai or in Cowork). It does not work in cloud sessions (the audio would play on the server) or inside WSL.

> **Language:** the fixed phrases and the default voice are Spanish (Río de la Plata). The summary is written in whatever language you talk to Claude in. You can switch to any Piper voice (see Configuration).

## Install

```
/plugin marketplace add gastonnrodriguez/claude-voice
/plugin install voice@claude-voice
```

The first session installs the speech engine in the background (about 300 MB, once) and says it has its voice when done. Until then it uses the system voice.

Requirements: **Python 3.9 or later**.

| System | What else you need | Voice while installing |
|---|---|---|
| Linux | PipeWire or PulseAudio (standard on any desktop). `python3-venv` is not required. | `spd-say` |
| macOS | Nothing else. | `say` |
| Windows | Python from [python.org](https://www.python.org/downloads/) or `winget install Python.Python.3.12`. Claude Code must run hooks with Git Bash (the default when Git for Windows is installed). | Windows' built-in voice |

## Configuration

When you install it, the plugin asks **what it should call you**. Change it later from `/plugin`, in the options for **voice**. Empty uses the first name of your system account (no name on Windows); a dash (`-`) says no name at all.

Optionally, a `config.json`:

- Linux: `~/.config/claude-voice/config.json`
- macOS: `~/Library/Application Support/claude-voice/config.json`
- Windows: `%APPDATA%\claude-voice\config.json`

```json
{ "voz": "es_AR-daniela-high" }
```

`voz` takes any voice from the [Piper catalogue](https://huggingface.co/rhasspy/piper-voices), for example `en_US-lessac-high`.

## How it works

Three Claude Code hooks:

| Event | What it does |
|---|---|
| `SessionStart` | Adds one instruction to Claude's context: end replies with `<!-- voice: summary -->`. Starts the engine install if it is missing. |
| `Stop` | Says that Claude finished, the project name (from the folder) and the summary. |
| `Notification` | Says that Claude needs your attention, on permission prompts and questions. |

The hook returns in milliseconds; everything slow (synthesis and playback) runs in a background process.

## What it runs, downloads and stores

Everything the plugin does, so you can audit it. The source is plain, readable Python in `scripts/voice.py`, plus a small launcher in `scripts/voice.sh`.

**Runs locally**

- `scripts/voice.sh`, which finds a working Python 3.9+ (`python3`, `python` or `py -3`) and runs `scripts/voice.py` with only the standard library.
- An audio player: `afplay` (macOS), `winsound` (Windows), or `pw-play` / `paplay` / `aplay` (Linux).
- Until Piper is installed, the system voice: `spd-say` (Linux), `say` (macOS), or PowerShell's `System.Speech` (Windows).

**Downloads, once, on first use**

- Creates a Python virtual environment in the data folder and installs `piper-tts==1.8.0` and its dependencies from PyPI with `pip`.
- If that Python has no `ensurepip` (common on Ubuntu without `python3-venv`), it installs pip into the virtual environment from the system's pip, or failing that from `https://bootstrap.pypa.io/get-pip.py`.
- Downloads the voice model (default `es_AR-daniela-high`, about 114 MB) from the Piper voices repository on Hugging Face, through Piper's own `piper.download_voices`.

After that it makes no network connections. Speech is generated on your machine.

**Reads**

- The JSON that Claude Code passes to each hook, and, if the summary is not in it, the end of the session transcript file Claude Code points to, to find the `<!-- voice: ... -->` comment.
- Your `config.json`, if you created one.

**Writes**, only in its data folder (`~/.local/share/claude-voice` on Linux, `~/Library/Application Support/claude-voice` on macOS, `%LOCALAPPDATA%\claude-voice` on Windows):

- The virtual environment and the voice model.
- Cached audio of fixed phrases (`cache/`) and temporary audio of summaries (`tmp/`, deleted after playing).
- Lock files and a log, `claude-voice.log`. Look there if nothing plays.

It sends nothing anywhere: no analytics, no telemetry, and your summaries never leave your machine.

## Contributing

Bugs, ideas and voices that sound good in your language are welcome as [issues](https://github.com/gastonnrodriguez/claude-voice/issues), and pull requests too. Reports from macOS and Windows are especially useful.

## License

[Apache 2.0](https://github.com/gastonnrodriguez/claude-voice/blob/main/LICENSE)
