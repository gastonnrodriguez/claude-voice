#!/usr/bin/env python3
"""claude-voice — avisos por voz para Claude Code (Linux, macOS y Windows).

Los hooks llaman a este script; el script responde al instante y deja el
trabajo lento (generar voz, reproducir) a un proceso en segundo plano.
Solo usa la biblioteca estándar de Python.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

WINDOWS = os.name == "nt"
MAC = sys.platform == "darwin"
if WINDOWS:
    import msvcrt
    import winsound
else:
    import fcntl
    import pwd

HOME = os.path.expanduser("~")
if WINDOWS:
    _DATA = os.path.join(os.environ.get("LOCALAPPDATA", HOME), "claude-voice")
    CONF = os.path.join(os.environ.get("APPDATA", HOME), "claude-voice", "config.json")
elif MAC:
    _DATA = os.path.join(HOME, "Library", "Application Support", "claude-voice")
    CONF = os.path.join(_DATA, "config.json")
else:
    _DATA = os.path.join(HOME, ".local/share/claude-voice")
    CONF = os.path.join(HOME, ".config/claude-voice/config.json")
DATA = os.environ.get("CLAUDE_VOICE_HOME", _DATA)
CACHE = os.path.join(DATA, "cache")
TMP = os.path.join(DATA, "tmp")
LOG = os.path.join(DATA, "claude-voice.log")
VENV_PY = os.path.join(DATA, "venv", "Scripts", "python.exe") if WINDOWS else os.path.join(DATA, "venv/bin/python")
# Piper lee el texto por stdin: en Windows, sin esto, lo decodifica como cp1252 y rompe los acentos.
ENV_UTF8 = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
# En Windows, que los procesos auxiliares no abran una consola por cada aviso.
SIN_VENTANA = subprocess.CREATE_NO_WINDOW if WINDOWS else 0
# Versión fija: lo que se instala es siempre lo mismo que se probó.
PIPER = "piper-tts==1.8.0"
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
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(p) for p in partes) + "\n")


def bloquear(f, esperar=True):
    """Lock exclusivo sobre un archivo abierto; se libera al cerrarlo. False si está tomado y no esperamos."""
    if not WINDOWS:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | (0 if esperar else fcntl.LOCK_NB))
            return True
        except OSError:
            return False
    while True:
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            if not esperar:
                return False
            time.sleep(0.2)


def nombre_del_sistema():
    """Primer nombre de la cuenta del sistema (en Windows no hay uno confiable)."""
    if WINDOWS:
        return ""
    try:
        return pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0].split()[0]
    except Exception:
        return ""


def config():
    c = {"voz": "es_AR-daniela-high", "nombre": None}
    try:
        with open(CONF, encoding="utf-8") as f:
            c.update(json.load(f))
    except Exception:
        pass
    # Lo que el usuario eligió al instalar el plugin (userConfig) manda sobre el archivo.
    elegido = os.environ.get("CLAUDE_PLUGIN_OPTION_NOMBRE", "").strip()
    if elegido:
        c["nombre"] = "" if elegido == "-" else elegido
    if c["nombre"] is None:
        c["nombre"] = nombre_del_sistema()
    return c


def modelo(c):
    return os.path.join(DATA, c["voz"] + ".onnx")


def instalado(c):
    return os.path.exists(VENV_PY) and os.path.exists(modelo(c))


# ---------- instalación (una sola vez, en segundo plano) ----------

def correr(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=ENV_UTF8, creationflags=SIN_VENTANA)
    if r.returncode != 0:
        log("ERROR instalando:", " ".join(cmd), "\n", (r.stderr or r.stdout)[-800:])
    return r.returncode == 0


def crear_venv(venv):
    """Crea el venv con pip. Sin python3-venv (falta ensurepip, típico de Ubuntu),
    lo crea sin pip y se lo instala aparte, así no hace falta sudo."""
    if subprocess.run([sys.executable, "-m", "venv", venv], capture_output=True,
                      creationflags=SIN_VENTANA).returncode == 0:
        return True
    shutil.rmtree(venv, ignore_errors=True)
    if not correr([sys.executable, "-m", "venv", "--without-pip", venv]):
        return False
    log("sin ensurepip: instalo pip en el venv por separado")
    # Si el sistema tiene pip (python3-pip), lo usamos para instalar pip dentro del venv.
    if correr([sys.executable, "-m", "pip", "--python", VENV_PY, "install", "-q", "pip"]):
        return True
    get_pip = os.path.join(DATA, "get-pip.py")
    try:
        urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", get_pip)
        ok = correr([VENV_PY, "-I", get_pip, "-q"])
    except Exception as e:
        log("ERROR bajando get-pip.py:", repr(e))
        ok = False
    try:
        os.remove(get_pip)
    except OSError:
        pass
    return ok


def instalar(c):
    os.makedirs(DATA, exist_ok=True)
    lock = open(os.path.join(DATA, "instalar.lock"), "w")
    if not bloquear(lock, esperar=False):
        return  # otra sesión ya está instalando
    if instalado(c):
        return
    log("instalando motor de voz", c["voz"])
    venv = os.path.join(DATA, "venv")
    # Un intento anterior sin python3-venv deja un venv roto, sin pip: lo rehacemos.
    if os.path.exists(VENV_PY) and subprocess.run([VENV_PY, "-m", "pip", "--version"], capture_output=True,
                                                  creationflags=SIN_VENTANA).returncode != 0:
        shutil.rmtree(venv, ignore_errors=True)
    if not os.path.exists(VENV_PY) and not crear_venv(venv):
        shutil.rmtree(venv, ignore_errors=True)
        if WINDOWS or MAC:
            log("No pude crear el entorno de Python. Instalá Python 3 desde https://www.python.org/downloads/")
        else:
            paquete = "python%d.%d-venv" % sys.version_info[:2]
            log(f"No pude crear el entorno de Python. Solución: sudo apt install {paquete}")
        hablar_sistema("Para tener la voz buena, me falta algo de Python. Mirá el log.")
        return
    pasos = [
        [VENV_PY, "-m", "pip", "install", "-q", PIPER],
        [VENV_PY, "-m", "piper.download_voices", "--download-dir", DATA, c["voz"]],
    ]
    for cmd in pasos:
        if not correr(cmd):
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
                       input=texto.encode("utf-8"), capture_output=True, env=ENV_UTF8, creationflags=SIN_VENTANA)
    if r.returncode != 0 or not os.path.exists(parcial):
        log("ERROR piper:", r.stderr.decode(errors="ignore")[-300:])
        return None
    os.replace(parcial, destino)
    return destino


def reproducir(ruta):
    if WINDOWS:
        winsound.PlaySound(ruta, winsound.SND_FILENAME)
        return
    for cmd in (["afplay"], ["pw-play"], ["paplay"], ["aplay", "-q"]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd + [ruta], capture_output=True)
            return
    log("ERROR: no encontré reproductor de audio (afplay, pw-play, paplay, aplay)")


VOZ_WINDOWS = (
    "Add-Type -AssemblyName System.Speech;"
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
    "$v = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'es*' } | Select-Object -First 1;"
    "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) };"
    "$s.Speak($env:CLAUDE_VOICE_TEXTO)"
)


def voz_espanol_mac():
    """Una voz en español de macOS (por ejemplo Paulina o Mónica), si hay alguna instalada."""
    r = subprocess.run(["say", "-v", "?"], capture_output=True, text=True)
    for linea in r.stdout.splitlines():
        m = re.match(r"(.+?)\s+(es_\w+)\s", linea)
        if m:
            return m.group(1).strip()
    return None


def hablar_sistema(texto):
    """Voz de emergencia mientras Piper no está instalado."""
    if WINDOWS:
        # El texto viaja por variable de entorno para no tener que escaparlo en PowerShell.
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", VOZ_WINDOWS],
                       env=dict(os.environ, CLAUDE_VOICE_TEXTO=texto), capture_output=True,
                       creationflags=SIN_VENTANA)
    elif MAC:
        voz = voz_espanol_mac()
        subprocess.run(["say"] + (["-v", voz] if voz else []) + [texto], capture_output=True)
    elif shutil.which("spd-say"):
        subprocess.run(["spd-say", "-w", "-l", "es", texto], capture_output=True)
    else:
        log("sin motor de voz todavía; no pude decir:", texto)


def hablar(c, partes):
    """partes: lista de (texto, guardar_en_cache). Habla de a una sesión por vez."""
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "hablar.lock"), "w") as lock:
        bloquear(lock)  # cola: si dos agentes terminan juntos, esperan turno
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
    r = datos.get("resumen", "")
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
    log("evento", evento)
    if evento in ("stop", "notification") and not instalado(c):
        # No dependemos solo de SessionStart: cualquier aviso dispara la instalación pendiente.
        en_segundo_plano("instalar", {})
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
    # Solo lo que usa trabajo(): la respuesta entera de Claude puede superar el límite de
    # largo de la línea de comandos (32 KB en Windows).
    datos = {"cwd": datos.get("cwd"), "transcript_path": datos.get("transcript_path"),
             "resumen": resumen_de(datos.get("last_assistant_message", ""))}
    cmd = [sys.executable, os.path.abspath(__file__), "_trabajo", evento, json.dumps(datos)]
    opciones = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not WINDOWS:
        subprocess.Popen(cmd, start_new_session=True, **opciones)
        return
    # Que sobreviva al hook: proceso aparte, sin consola y, si se puede, fuera del job de Claude Code.
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    try:
        subprocess.Popen(cmd, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **opciones)
    except OSError:
        subprocess.Popen(cmd, creationflags=flags, **opciones)


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
        trabajo("stop", {"cwd": os.getcwd(), "resumen": "esto es una prueba"})
        return
    try:
        datos = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except Exception:
        datos = {}
    if evento == "session-start":
        if not instalado(config()):
            en_segundo_plano("instalar", {})
        # SessionStart: lo que se imprime se agrega al contexto de Claude. En UTF-8 aunque Windows use cp1252.
        sys.stdout.buffer.write((INSTRUCCION + "\n").encode("utf-8"))
    elif evento in ("stop", "notification"):
        en_segundo_plano(evento, datos)


if __name__ == "__main__":
    main()
