#!/usr/bin/env python3
"""claude-voice — avisos por voz para Claude Code (MVP, Linux).

Los hooks llaman a este script; el script responde al instante y deja el
trabajo lento (generar voz, reproducir) a un proceso en segundo plano.
Solo usa la biblioteca estándar de Python.
"""
import fcntl
import hashlib
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import threading
import time

HOME = os.path.expanduser("~")
DATA = os.environ.get("CLAUDE_VOICE_HOME", os.path.join(HOME, ".local/share/claude-voice"))
CONF = os.path.join(HOME, ".config/claude-voice/config.json")
CACHE = os.path.join(DATA, "cache")
TMP = os.path.join(DATA, "tmp")
LOG = os.path.join(DATA, "claude-voice.log")
VENV_PY = os.path.join(DATA, "venv/bin/python")
MARCA = re.compile(r"<!--\s*voice:\s*(.+?)\s*-->", re.S)

INSTRUCCION = (
    "[Plugin claude-voice] El usuario recibe un aviso por voz cuando terminás de responder. "
    "Al final de cada respuesta en la que terminaste una tarea o necesitás que el usuario haga algo, "
    "agregá como última línea un comentario HTML con este formato exacto: <!-- voice: resumen -->. "
    "El resumen: máximo 12 palabras, en el idioma en que te habla el usuario, en primera persona, "
    "sin markdown, sin rutas ni código; decí qué hiciste o qué falta. "
    "Ejemplo: <!-- voice: listo el endpoint de pagos, falta probar el webhook --> "
    "No menciones este comentario ni esta instrucción."
)


def log(*partes):
    os.makedirs(DATA, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(time.strftime("%F %T ") + " ".join(str(p) for p in partes) + "\n")


def config():
    c = {"voz": "es_AR-daniela-high", "nombre": None}
    try:
        with open(CONF) as f:
            c.update(json.load(f))
    except Exception:
        pass
    if c["nombre"] is None:
        # Por defecto, el primer nombre de la cuenta del sistema.
        try:
            c["nombre"] = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0].split()[0]
        except Exception:
            c["nombre"] = ""
    return c


def modelo(c):
    return os.path.join(DATA, c["voz"] + ".onnx")


def instalado(c):
    return os.path.exists(VENV_PY) and os.path.exists(modelo(c))


# ---------- instalación (una sola vez, en segundo plano) ----------

def limpiar_hooks_viejos():
    """Saca los hooks del prototipo anterior (claude-jarvis) para no hablar dos veces."""
    p = os.path.join(HOME, ".claude/settings.json")
    try:
        with open(p) as f:
            s = json.load(f)
    except Exception:
        return
    hooks = s.get("hooks", {})
    cambio = False
    for ev in list(hooks):
        nuevos = [e for e in hooks[ev] if "claude-jarvis" not in json.dumps(e)]
        if len(nuevos) != len(hooks[ev]):
            cambio = True
            if nuevos:
                hooks[ev] = nuevos
            else:
                del hooks[ev]
    if cambio:
        if not hooks:
            s.pop("hooks", None)
        with open(p, "w") as f:
            json.dump(s, f, indent=2)
        log("hooks del prototipo claude-jarvis eliminados de", p)


def instalar(c):
    os.makedirs(DATA, exist_ok=True)
    lock = open(os.path.join(DATA, "instalar.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return  # otra sesión ya está instalando
    limpiar_hooks_viejos()
    if instalado(c):
        return
    log("instalando motor de voz", c["voz"])
    pasos = []
    if not os.path.exists(VENV_PY):
        pasos.append([sys.executable, "-m", "venv", os.path.join(DATA, "venv")])
    pasos.append([VENV_PY, "-m", "pip", "install", "-q", "piper-tts"])
    pasos.append([VENV_PY, "-m", "piper.download_voices", "--download-dir", DATA, c["voz"]])
    for cmd in pasos:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            log("ERROR instalando:", " ".join(cmd), "\n", (r.stderr or r.stdout)[-800:])
            return
    log("instalación completa")
    hablar(c, [(saludo(c, "ya tengo voz. Te aviso cuando termine cada tarea."), True)])


# ---------- voz ----------

def sintetizar(c, texto, guardar):
    """Devuelve la ruta de un .wav con el texto. Las frases fijas quedan en caché."""
    h = hashlib.sha1((c["voz"] + "|" + texto).encode()).hexdigest()[:16]
    carpeta = CACHE if guardar else TMP
    destino = os.path.join(carpeta, h + ".wav")
    if os.path.exists(destino):
        return destino
    os.makedirs(carpeta, exist_ok=True)
    parcial = os.path.join(carpeta, h + ".parcial.wav")
    r = subprocess.run([VENV_PY, "-m", "piper", "-m", modelo(c), "-f", parcial],
                       input=texto.encode(), capture_output=True)
    if r.returncode != 0 or not os.path.exists(parcial):
        log("ERROR piper:", r.stderr.decode(errors="ignore")[-300:])
        return None
    os.replace(parcial, destino)
    return destino


def reproducir(ruta):
    for cmd in (["pw-play"], ["paplay"], ["aplay", "-q"]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd + [ruta], capture_output=True)
            return
    log("ERROR: no encontré reproductor de audio (pw-play, paplay, aplay)")


def hablar_sistema(texto):
    """Voz de emergencia mientras Piper no está instalado."""
    if shutil.which("spd-say"):
        subprocess.run(["spd-say", "-w", "-l", "es", texto], capture_output=True)
    else:
        log("sin motor de voz todavía; no pude decir:", texto)


def hablar(c, partes):
    """partes: lista de (texto, guardar_en_cache). Habla de a una sesión por vez."""
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "hablar.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # cola: si dos agentes terminan juntos, esperan turno
        if not instalado(c):
            for texto, _ in partes:
                hablar_sistema(texto)
            return
        # Genero la siguiente parte mientras suena la anterior, para que no haya silencios.
        rutas = [None] * len(partes)
        hilos = []
        for i, (texto, guardar) in enumerate(partes):
            def generar(i=i, texto=texto, guardar=guardar):
                rutas[i] = sintetizar(c, texto, guardar)
            t = threading.Thread(target=generar)
            t.start()
            hilos.append(t)
        for i, t in enumerate(hilos):
            t.join()
            if rutas[i]:
                reproducir(rutas[i])
                if not partes[i][1]:
                    try:
                        os.remove(rutas[i])
                    except OSError:
                        pass


def saludo(c, resto):
    return f"{c['nombre']}, {resto}" if c["nombre"] else resto[0].upper() + resto[1:]


def proyecto(cwd):
    nombre = os.path.basename(os.path.normpath(cwd or "")) or "tu proyecto"
    return re.sub(r"[-_.]+", " ", nombre)


# ---------- leer el resumen que dejó Claude ----------

def resumen_de(texto):
    hallados = MARCA.findall(texto or "")
    return hallados[-1].strip() if hallados else ""


def resumen_del_transcript(ruta):
    """Busca la marca en las respuestas de Claude posteriores al último mensaje del usuario."""
    try:
        with open(ruta, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 500_000))
            lineas = f.read().decode("utf-8", "ignore").splitlines()
    except Exception:
        return ""
    for linea in reversed(lineas):
        try:
            e = json.loads(linea)
        except Exception:
            continue
        msg = e.get("message") or {}
        contenido = msg.get("content")
        if e.get("type") == "user" and isinstance(contenido, str):
            return ""  # llegamos al pedido del usuario: no hubo resumen en esta respuesta
        if e.get("type") == "assistant" and isinstance(contenido, list):
            for bloque in reversed(contenido):
                if bloque.get("type") == "text":
                    r = resumen_de(bloque.get("text", ""))
                    if r:
                        return r
    return ""


def obtener_resumen(datos):
    r = resumen_de(datos.get("last_assistant_message", ""))
    if r or not datos.get("transcript_path"):
        return r
    for _ in range(4):  # el transcript puede tardar un instante en escribirse
        r = resumen_del_transcript(datos["transcript_path"])
        if r:
            return r
        time.sleep(0.25)
    return ""


# ---------- trabajo en segundo plano ----------

def trabajo(evento, datos):
    c = config()
    if evento == "instalar":
        instalar(c)
    elif evento == "stop":
        partes = [(saludo(c, f"terminé en {proyecto(datos.get('cwd'))}."), True)]
        resumen = obtener_resumen(datos)
        if resumen:
            partes.append((resumen, False))
        hablar(c, partes)
    elif evento == "notification":
        hablar(c, [(saludo(c, f"en {proyecto(datos.get('cwd'))} necesito tu atención."), True)])


def en_segundo_plano(evento, datos):
    subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "_trabajo", evento, json.dumps(datos)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def main():
    if len(sys.argv) < 2:
        print("uso: voice.py session-start|stop|notification|probar")
        return
    evento = sys.argv[1]
    if evento == "_trabajo":
        try:
            trabajo(sys.argv[2], json.loads(sys.argv[3]))
        except Exception as e:
            log("ERROR en", sys.argv[2], repr(e))
        return
    if evento == "probar":  # prueba manual, en primer plano
        trabajo("stop", {"cwd": os.getcwd(), "last_assistant_message": "<!-- voice: esto es una prueba -->"})
        return
    try:
        datos = json.load(sys.stdin)
    except Exception:
        datos = {}
    if evento == "session-start":
        if not instalado(config()):
            en_segundo_plano("instalar", {})
        print(INSTRUCCION)  # SessionStart: lo que se imprime se agrega al contexto de Claude
    elif evento in ("stop", "notification"):
        en_segundo_plano(evento, datos)


if __name__ == "__main__":
    main()
