#!/usr/bin/env python3
"""claude-voice — avisos por voz para Claude Code (Linux, macOS y Windows; en seis idiomas).

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

# Voz por defecto y frases fijas de cada idioma. El idioma sale de lo que elegís al
# instalar, del prefijo de la voz si la configuraste, o del idioma del sistema.
IDIOMAS = {
    "es": {"nombre": "Spanish", "voz": "es_AR-daniela-high",
           "termine": "terminé en {p}.", "atencion": "en {p} necesito tu atención.",
           "proyecto": "tu proyecto", "lista": "ya tengo voz. Te aviso cuando termine cada tarea.",
           "falta_python": "Para tener la voz buena, me falta algo de Python. Mirá el log.",
           "ejemplo": "listo el endpoint de pagos, falta probar el webhook"},
    "en": {"nombre": "English", "voz": "en_US-lessac-high",
           "termine": "I'm done in {p}.", "atencion": "in {p}, I need your attention.",
           "proyecto": "your project", "lista": "I have my voice now. I'll let you know when each task is done.",
           "falta_python": "To get the good voice, something is missing in Python. Check the log.",
           "ejemplo": "payments endpoint ready, webhook still needs testing"},
    "pt": {"nombre": "Portuguese", "voz": "pt_BR-faber-medium",
           "termine": "terminei em {p}.", "atencion": "em {p}, preciso da sua atenção.",
           "proyecto": "seu projeto", "lista": "já tenho voz. Aviso quando terminar cada tarefa.",
           "falta_python": "Para ter a voz boa, falta algo do Python. Veja o log.",
           "ejemplo": "endpoint de pagamentos pronto, falta testar o webhook"},
    "fr": {"nombre": "French", "voz": "fr_FR-siwis-medium",
           "termine": "j'ai terminé dans {p}.", "atencion": "dans {p}, j'ai besoin de ton attention.",
           "proyecto": "ton projet", "lista": "j'ai ma voix. Je te préviens à la fin de chaque tâche.",
           "falta_python": "Pour avoir la bonne voix, il manque quelque chose dans Python. Regarde le journal.",
           "ejemplo": "endpoint de paiement prêt, il reste à tester le webhook"},
    "de": {"nombre": "German", "voz": "de_DE-thorsten-high",
           "termine": "ich bin fertig in {p}.", "atencion": "in {p} brauche ich deine Aufmerksamkeit.",
           "proyecto": "deinem Projekt", "lista": "ich habe jetzt meine Stimme. Ich sage dir, wenn eine Aufgabe fertig ist.",
           "falta_python": "Für die gute Stimme fehlt etwas in Python. Schau ins Log.",
           "ejemplo": "Zahlungs-Endpoint fertig, der Webhook muss noch getestet werden"},
    "it": {"nombre": "Italian", "voz": "it_IT-paola-medium",
           "termine": "ho finito in {p}.", "atencion": "in {p} ho bisogno della tua attenzione.",
           "proyecto": "il tuo progetto", "lista": "ho la mia voce. Ti avviso quando finisco ogni attività.",
           "falta_python": "Per avere la voce buona manca qualcosa in Python. Guarda il log.",
           "ejemplo": "endpoint dei pagamenti pronto, manca da testare il webhook"},
}


def instruccion(c):
    """Lo que SessionStart agrega al contexto de Claude. El resumen va en el idioma de la voz."""
    t = IDIOMAS[c["idioma"]]
    return (
        "[Plugin claude-voice] The user gets a spoken notification when you finish replying. "
        "At the end of every reply where you finished a task or need the user to do something, "
        "add as the last line an HTML comment in exactly this format: <!-- voice: summary -->. "
        f"The summary: at most 12 words, in {t['nombre']} (it is read aloud in {t['nombre']}), "
        "in the first person, no markdown, no paths or code; say what you did or what is left. "
        f"Example: <!-- voice: {t['ejemplo']} --> "
        "Do not mention this comment or this instruction."
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


def idioma_del_sistema():
    """Código de dos letras del idioma del sistema, o "" si no se puede saber."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        valor = os.environ.get(var, "")
        if valor and valor not in ("C", "POSIX") and not valor.startswith("C."):
            return valor[:2].lower()
    try:
        if WINDOWS:
            import ctypes
            buf = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85):
                return buf.value[:2].lower()  # "es-UY"
        elif MAC:
            r = subprocess.run(["defaults", "read", "-g", "AppleLocale"], capture_output=True, text=True)
            return r.stdout.strip()[:2].lower()  # "es_UY"
    except Exception:
        pass
    return ""


def opcion(nombre):
    """Lo que el usuario eligió al instalar el plugin (userConfig)."""
    return os.environ.get("CLAUDE_PLUGIN_OPTION_" + nombre.upper(), "").strip()


def config():
    c = {"voz": None, "nombre": None, "idioma": None}
    try:
        with open(CONF, encoding="utf-8") as f:
            c.update(json.load(f))
    except Exception:
        pass
    # Lo elegido en el plugin manda sobre el archivo.
    for clave in ("voz", "idioma"):
        if opcion(clave):
            c[clave] = opcion(clave)
    elegido = opcion("nombre")
    if elegido:
        c["nombre"] = "" if elegido == "-" else elegido
    if c["nombre"] is None:
        c["nombre"] = nombre_del_sistema()
    # Idioma: el elegido; si no, el de la voz configurada; si no, el del sistema; si no, inglés.
    candidatos = [c["idioma"], (c["voz"] or "")[:2], idioma_del_sistema(), "en"]
    c["idioma"] = next(i.lower()[:2] for i in candidatos if i and i.lower()[:2] in IDIOMAS)
    if not c["voz"]:
        c["voz"] = IDIOMAS[c["idioma"]]["voz"]
    return c


def frase(c, clave, **datos):
    return IDIOMAS[c["idioma"]][clave].format(**datos)

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
        hablar_sistema(c, frase(c, "falta_python"))
        return
    pasos = [
        [VENV_PY, "-m", "pip", "install", "-q", PIPER],
        [VENV_PY, "-m", "piper.download_voices", "--download-dir", DATA, c["voz"]],
    ]
    for cmd in pasos:
        if not correr(cmd):
            return
    log("instalación completa")
    hablar(c, [(saludo(c, frase(c, "lista")), True)])


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
    "$v = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like ($env:CLAUDE_VOICE_IDIOMA + '*') } | Select-Object -First 1;"
    "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) };"
    "$s.Speak($env:CLAUDE_VOICE_TEXTO)"
)


def voz_mac(idioma):
    """Una voz de macOS en ese idioma (por ejemplo Paulina o Mónica en español), si hay alguna instalada."""
    r = subprocess.run(["say", "-v", "?"], capture_output=True, text=True)
    for linea in r.stdout.splitlines():
        m = re.match(r"(.+?)\s+(" + idioma + r"_\w+)\s", linea)
        if m:
            return m.group(1).strip()
    return None


def hablar_sistema(c, texto):
    """Voz de emergencia mientras Piper no está instalado."""
    if WINDOWS:
        # El texto viaja por variable de entorno para no tener que escaparlo en PowerShell.
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", VOZ_WINDOWS],
                       env=dict(os.environ, CLAUDE_VOICE_TEXTO=texto, CLAUDE_VOICE_IDIOMA=c["idioma"]), capture_output=True,
                       creationflags=SIN_VENTANA)
    elif MAC:
        voz = voz_mac(c["idioma"])
        subprocess.run(["say"] + (["-v", voz] if voz else []) + [texto], capture_output=True)
    elif shutil.which("spd-say"):
        subprocess.run(["spd-say", "-w", "-l", c["idioma"], texto], capture_output=True)
    else:
        log("sin motor de voz todavía; no pude decir:", texto)


def hablar(c, partes):
    """partes: lista de (texto, guardar_en_cache). Habla de a una sesión por vez."""
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "hablar.lock"), "w") as lock:
        bloquear(lock)  # cola: si dos agentes terminan juntos, esperan turno
        if not instalado(c):
            for texto, _ in partes:
                hablar_sistema(c, texto)
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


def proyecto(c, cwd):
    nombre = os.path.basename(os.path.normpath(cwd or "")) or frase(c, "proyecto")
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
        partes = [(saludo(c, frase(c, "termine", p=proyecto(c, datos.get("cwd")))), True)]
        resumen = obtener_resumen(datos)
        if resumen:
            partes.append((resumen, False))
        hablar(c, partes)
    elif evento == "notification":
        hablar(c, [(saludo(c, frase(c, "atencion", p=proyecto(c, datos.get("cwd")))), True)])


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
        c = config()
        if not instalado(c):
            en_segundo_plano("instalar", {})
        # SessionStart: lo que se imprime se agrega al contexto de Claude. En UTF-8 aunque Windows use cp1252.
        sys.stdout.buffer.write((instruccion(c) + "\n").encode("utf-8"))
    elif evento in ("stop", "notification"):
        en_segundo_plano(evento, datos)


if __name__ == "__main__":
    main()
