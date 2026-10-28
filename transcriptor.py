#!/usr/bin/env python3
"""
Transcriptor automatico de notas de voz.

Vigila la app Notas de Voz del Mac (y, como respaldo, la carpeta de Audio
en iCloud). Cuando aparece una grabacion nueva, la transcribe en local con
MLX Whisper y guarda el texto en la carpeta de Transcripciones. No sube
nada a internet y no usa API keys.

Para probarlo a mano una sola vez, sin dejarlo corriendo:
    ~/Diario-transcriptor/.venv/bin/python ~/Diario-transcriptor/transcriptor.py --una-vez
"""

import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuracion. Esto es lo unico que normalmente querras cambiar.
# ---------------------------------------------------------------------------

# Carpeta del diario. Es la que se ve como "Documentos > Personal > Diario"
# en el Finder, que iCloud sincroniza sola. No confundir con
# "Documentos > Diario".
DIARIO = Path.home() / "Documents/Personal/Diario"
CARPETA_TEXTO = DIARIO / "Transcripciones"
CARPETA_TEXTO_OTRAS = CARPETA_TEXTO / "Otras"
ARCHIVO_LOG = DIARIO / "transcriptor.log"

# Fuente principal: la app Notas de Voz del Mac. iCloud deja aqui los .m4a
# que grabas en el iPhone. Los titulos de las grabaciones no estan en el
# nombre del archivo sino en una base de datos que vive en la misma carpeta.
CARPETA_NOTAS_DE_VOZ = (
    Path.home() / "Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings"
)
BASE_DE_DATOS = CARPETA_NOTAS_DE_VOZ / "CloudRecordings.db"

# Fuente de respaldo: la carpeta donde antes el Atajo del iPhone dejaba los
# audios. Se sigue vigilando por si algun dia vuelves a usarla.
CARPETA_AUDIO = DIARIO / "Audio"

# Las grabaciones cuyo titulo empieza asi van a Transcripciones/. Las demas
# van a Transcripciones/Otras/. No distingue mayusculas de minusculas.
PREFIJO_DIARIO = "diario"

# Titulos que la app pone sola cuando no le das nombre. Si una grabacion se
# llama asi (o asi mas un numero, tipo "New Recording 2"), el texto se
# nombra con la fecha y hora de la grabacion en vez de con el titulo.
TITULOS_GENERICOS = {
    "new recording",
    "nueva grabacion",
    "grabacion nueva",
    "recording",
    "grabacion",
}

# Modelo de Whisper optimizado para Apple Silicon. Se descarga solo la
# primera vez y queda guardado en ~/.cache/huggingface.
# Desde el 2026-10-13 usa large-v3 completo en vez de turbo: más lento, pero
# comete menos errores en español. Para volver atrás: whisper-large-v3-turbo.
MODELO = "mlx-community/whisper-large-v3-mlx"

# Forzamos espanol. Probado con audio real: con la deteccion automatica,
# un audio que mezcla espanol e ingles se detecta como ingles y la
# transcripcion sale medio traducida y rota. Forzando "es" sale correcta
# y ademas conserva los terminos en ingles tal cual se dicen.
IDIOMA = "es"

# Cada cuantos segundos se revisan las fuentes buscando audios nuevos.
SEGUNDOS_ENTRE_REVISIONES = 30

# Extensiones de audio que aceptamos en la carpeta de respaldo.
EXTENSIONES = {".m4a", ".mp3", ".wav", ".aac", ".mp4", ".caf", ".aiff"}

# Cada cuantos segundos se pone una marca de tiempo en el texto, para
# poder ubicar despues ese momento en el audio. 60 = una marca por minuto.
MARCA_CADA_SEGUNDOS = 60

# Cuantas veces se reintenta un audio que da error antes de rendirse.
MAX_INTENTOS = 3

# Carpetas donde viven los programas instalados con Homebrew. Whisper
# necesita ffmpeg para abrir los .m4a, y cuando macOS arranca esto solo
# al encender el Mac le da una lista de carpetas muy corta que no
# incluye las de Homebrew. Por eso se las anadimos nosotros.
CARPETAS_DE_HERRAMIENTAS = ("/opt/homebrew/bin", "/usr/local/bin")

# ---------------------------------------------------------------------------


log = logging.getLogger("transcriptor")

# Las fechas de la base de datos de Notas de Voz se cuentan en segundos
# desde el 1 de enero de 2001 (asi lo hace Apple), no desde 1970.
EPOCA_APPLE = datetime(2001, 1, 1, tzinfo=timezone.utc)

# Para no repetir en el log el mismo aviso en cada vuelta.
_ya_avisado: set = set()


@dataclass
class Trabajo:
    """Un audio que hay que transcribir y donde dejar el texto."""

    audio: Path
    salida: Path
    etiqueta: str  # como lo llamamos en el log


def configurar_log() -> None:
    """Escribe los mensajes al archivo de log y tambien a la pantalla."""
    ARCHIVO_LOG.parent.mkdir(parents=True, exist_ok=True)
    formato = logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    log.setLevel(logging.INFO)

    a_archivo = logging.FileHandler(ARCHIVO_LOG, encoding="utf-8")
    a_archivo.setFormatter(formato)
    log.addHandler(a_archivo)

    a_pantalla = logging.StreamHandler(sys.stdout)
    a_pantalla.setFormatter(formato)
    log.addHandler(a_pantalla)


def avisar_una_vez(clave: str, mensaje: str, *args) -> None:
    """Escribe un aviso en el log solo la primera vez que ocurre."""
    if clave in _ya_avisado:
        return
    _ya_avisado.add(clave)
    log.warning(mensaje, *args)


def que_autorizar() -> Path:
    """
    Devuelve lo que hay que anadir en Full Disk Access.

    Si el programa corre dentro de Transcriptor.app, es la app entera.
    Si se lanzo a mano con el Python del entorno, es ese Python.
    """
    ejecutable = Path(sys.executable).resolve()
    for padre in ejecutable.parents:
        if padre.suffix == ".app":
            return padre
    return ejecutable


def asegurar_herramientas() -> None:
    """
    Se asegura de que ffmpeg se pueda encontrar, y avisa si falta.

    Sin ffmpeg, Whisper no puede abrir los archivos .m4a del iPhone.
    """
    carpetas = os.environ.get("PATH", "").split(":")
    for carpeta in CARPETAS_DE_HERRAMIENTAS:
        if carpeta not in carpetas:
            carpetas.append(carpeta)
    os.environ["PATH"] = ":".join(c for c in carpetas if c)

    if shutil.which("ffmpeg") is None:
        log.error(
            "No encuentro ffmpeg. Instalalo con: brew install ffmpeg. "
            "Sin el no se pueden leer los audios .m4a."
        )


# ---------------------------------------------------------------------------
# Fuente principal: Notas de Voz
# ---------------------------------------------------------------------------


def limpiar_titulo(titulo: str) -> str:
    """
    Convierte un titulo en algo que sirva como nombre de archivo.

    Quita los caracteres que macOS o iCloud no aceptan en un nombre
    (barras, dos puntos, comillas...) y los cambia por un guion.
    "Diario 2026-09-03 12:51" se convierte en "Diario 2026-09-03 12-51".
    """
    limpio = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "-", titulo)
    limpio = re.sub(r"\s+", " ", limpio).strip().strip(".")
    return limpio


def es_titulo_generico(titulo: str) -> bool:
    """True si es un nombre que la app puso sola, tipo "New Recording 3"."""
    base = titulo.strip().lower()
    base = base.replace("ó", "o").replace("ú", "u").replace("á", "a")
    base = re.sub(r"\s+\d+$", "", base)  # "new recording 3" -> "new recording"
    return base in TITULOS_GENERICOS


def nombre_del_texto(titulo: str, fecha: datetime) -> str:
    """Decide como se va a llamar el .txt de una grabacion."""
    if not es_titulo_generico(titulo):
        limpio = limpiar_titulo(titulo)
        if limpio:
            return limpio + ".txt"
    return fecha.strftime("%Y-%m-%d_%H-%M") + ".txt"


def carpeta_destino(titulo: str) -> Path:
    """Los "Diario ..." van a Transcripciones/, el resto a Otras/."""
    if titulo.strip().lower().startswith(PREFIJO_DIARIO):
        return CARPETA_TEXTO
    return CARPETA_TEXTO_OTRAS


def leer_notas_de_voz() -> list:
    """
    Lee la lista de grabaciones de la app Notas de Voz.

    La app guarda los titulos en una base de datos SQLite. Nunca la abrimos
    directamente: la copiamos a una carpeta temporal y leemos la copia en
    modo solo lectura, para no molestar a la app ni arriesgarnos a tocar
    nada. La copia se borra sola al terminar.

    Devuelve una lista de tuplas (titulo, ruta_del_audio, fecha).
    """
    if not BASE_DE_DATOS.exists():
        avisar_una_vez(
            "sin-db",
            "No encuentro la base de datos de Notas de Voz en %s. "
            "Sigo vigilando solo la carpeta de Audio.",
            BASE_DE_DATOS,
        )
        return []

    with tempfile.TemporaryDirectory(prefix="notas-de-voz-") as temporal:
        # SQLite reparte los datos entre el .db y dos archivos auxiliares
        # (-wal y -shm). Hay que copiar los tres para ver lo mas reciente.
        for sufijo in ("", "-wal", "-shm"):
            origen = BASE_DE_DATOS.with_name(BASE_DE_DATOS.name + sufijo)
            if origen.exists():
                shutil.copy2(origen, Path(temporal) / origen.name)

        copia = Path(temporal) / BASE_DE_DATOS.name
        conexion = sqlite3.connect(f"file:{copia}?mode=ro", uri=True)
        try:
            filas = conexion.execute(
                """
                SELECT ZENCRYPTEDTITLE, ZPATH, ZDATE
                FROM ZCLOUDRECORDING
                WHERE ZPATH IS NOT NULL
                  AND ZEVICTIONDATE IS NULL   -- las de "Eliminadas recientemente" no
                ORDER BY ZDATE
                """
            ).fetchall()
        finally:
            conexion.close()

    grabaciones = []
    for titulo, ruta, segundos in filas:
        titulo = (titulo or "").strip()
        fecha = (EPOCA_APPLE + timedelta(seconds=segundos or 0)).astimezone()
        grabaciones.append((titulo, CARPETA_NOTAS_DE_VOZ / ruta, fecha))
    return grabaciones


def pendientes_de_notas_de_voz() -> list:
    """Grabaciones de Notas de Voz que todavia no tienen su texto."""
    pendientes = []
    for titulo, audio, fecha in leer_notas_de_voz():
        salida = carpeta_destino(titulo) / nombre_del_texto(titulo, fecha)
        if salida.exists():
            continue
        if not audio.exists():
            # El titulo ya llego por iCloud pero el audio todavia no. Suele
            # resolverse solo en unos segundos; si no, hay que abrir la app
            # Notas de Voz en el Mac para que termine de bajarlo.
            avisar_una_vez(
                f"sin-audio:{audio.name}",
                "'%s' aparece en Notas de Voz pero su audio aun no esta en el Mac. "
                "Espero a que iCloud lo baje.",
                titulo,
            )
            continue
        pendientes.append(Trabajo(audio=audio, salida=salida, etiqueta=titulo))
    return pendientes


# ---------------------------------------------------------------------------
# Fuente de respaldo: la carpeta Audio/ en iCloud
# ---------------------------------------------------------------------------


def pedir_descarga_a_icloud(destino: Path) -> None:
    """
    Le pide a iCloud que baje un archivo que todavia esta solo en la nube.

    Cuando un archivo no esta descargado, iCloud deja en su lugar un
    marcador oculto que se llama igual pero empieza por punto y termina
    en .icloud. El comando brctl le dice a iCloud "bajame este de verdad".
    """
    try:
        subprocess.run(
            ["/usr/bin/brctl", "download", str(destino)],
            capture_output=True,
            timeout=30,
        )
    except Exception as error:  # noqa: BLE001 - nunca queremos que esto tumbe el bucle
        log.warning("No se pudo pedir la descarga de %s: %s", destino.name, error)


def revisar_marcadores_de_icloud() -> None:
    """Busca marcadores .icloud en la carpeta de audio y pide su descarga."""
    for marcador in CARPETA_AUDIO.glob(".*.icloud"):
        # ".2026-09-02_10-30.m4a.icloud"  ->  "2026-09-02_10-30.m4a"
        nombre_real = marcador.name[1:-len(".icloud")]
        destino = CARPETA_AUDIO / nombre_real
        if destino.exists():
            continue
        log.info("Aun en la nube, pidiendo descarga: %s", nombre_real)
        pedir_descarga_a_icloud(destino)


def pendientes_de_carpeta_audio() -> list:
    """Audios de la carpeta de respaldo que todavia no tienen su texto."""
    pendientes = []
    for audio in sorted(CARPETA_AUDIO.iterdir()):
        if not audio.is_file() or audio.name.startswith("."):
            continue
        if audio.suffix.lower() not in EXTENSIONES:
            continue
        salida = CARPETA_TEXTO / (audio.stem + ".txt")
        if salida.exists():
            continue
        pendientes.append(Trabajo(audio=audio, salida=salida, etiqueta=audio.name))
    return pendientes


# ---------------------------------------------------------------------------
# Transcripcion
# ---------------------------------------------------------------------------


def esta_completo(audio: Path, tamanos_previos: dict) -> bool:
    """
    Decide si el archivo ya termino de bajar o de copiarse.

    Comprueba dos cosas: que se puede leer entero, y que su tamano no ha
    cambiado desde la revision anterior. Si todavia esta creciendo, es
    que iCloud sigue bajandolo (o sigues grabando), asi que esperamos a
    la proxima vuelta.
    """
    try:
        tamano = audio.stat().st_size
    except OSError:
        return False

    if tamano == 0:
        return False

    clave = str(audio)
    anterior = tamanos_previos.get(clave)
    tamanos_previos[clave] = tamano
    if anterior != tamano:
        log.info("Todavia bajando o copiandose, espero: %s", audio.name)
        return False

    # Leemos el ultimo trozo del archivo. Si iCloud aun no lo tiene
    # completo, esta lectura falla y sabemos que no esta listo.
    try:
        with audio.open("rb") as f:
            f.seek(max(0, tamano - 4096))
            f.read()
    except OSError as error:
        log.info("Aun no se puede leer completo, espero: %s (%s)", audio.name, error)
        return False

    return True


def formatear_tiempo(segundos: float) -> str:
    """Convierte 185 segundos en "03:05", y si pasa de una hora en "1:03:05"."""
    total = int(segundos)
    horas, resto = divmod(total, 3600)
    minutos, segs = divmod(resto, 60)
    if horas:
        return f"{horas}:{minutos:02d}:{segs:02d}"
    return f"{minutos:02d}:{segs:02d}"


def texto_en_parrafos(resultado: dict) -> str:
    """
    Convierte la salida de Whisper en texto legible y con marcas de tiempo.

    Whisper devuelve el audio partido en trocitos, cada uno con el minuto y
    segundo en que se dijo. Con eso hacemos dos cosas:

    - Cada vez que empieza un minuto nuevo, ponemos una marca tipo [03:00]
      sola en su linea. Asi, si al leer encuentras algo y quieres volver a
      escucharlo, sabes a que altura del audio adelantar.
    - Dentro de cada minuto, cuando entre un trozo y el siguiente hay mas de
      dos segundos de silencio, empezamos un parrafo nuevo. Asi un volcado
      de media hora no queda como un unico bloque imposible de leer.
    """
    segmentos = resultado.get("segments") or []
    if not segmentos:
        return resultado.get("text", "").strip()

    bloques = []
    actual = []
    fin_anterior = None
    ultima_marca = -1

    def cerrar_parrafo() -> None:
        if actual:
            bloques.append(" ".join(actual).strip())
            actual.clear()

    for segmento in segmentos:
        trozo = segmento.get("text", "").strip()
        if not trozo:
            continue

        inicio = segmento.get("start") or 0
        marca = int(inicio // MARCA_CADA_SEGUNDOS)

        if marca > ultima_marca:
            cerrar_parrafo()
            bloques.append(f"[{formatear_tiempo(marca * MARCA_CADA_SEGUNDOS)}]")
            ultima_marca = marca
        elif fin_anterior is not None and inicio - fin_anterior > 2.0:
            cerrar_parrafo()

        actual.append(trozo)
        fin_anterior = segmento.get("end", fin_anterior)

    cerrar_parrafo()

    return "\n\n".join(b for b in bloques if b)


def transcribir(trabajo: Trabajo) -> None:
    """Transcribe un audio y guarda el texto."""
    # Importamos aqui dentro y no arriba para que el programa arranque
    # ligero: el modelo solo se carga en memoria cuando hay trabajo real.
    import mlx_whisper

    log.info("Transcribiendo: %s", trabajo.etiqueta)
    empezado = time.time()

    resultado = mlx_whisper.transcribe(
        str(trabajo.audio),
        path_or_hf_repo=MODELO,
        language=IDIOMA,
        # Que Whisper no use el texto anterior como contexto. Si lo usa, un
        # error (p. ej. "Gracias." repetido) se alimenta solo y en audios
        # largos puede saltarse minutos enteros. Pasó en una llamada de 110
        # minutos el 2026-10-19: se perdieron los minutos 33 a 53.
        condition_on_previous_text=False,
        verbose=None,  # None = sin barra de progreso, para que el log quede limpio
    )

    texto = texto_en_parrafos(resultado)

    # Escribimos primero en un archivo temporal y luego lo renombramos.
    # Renombrar es instantaneo, asi iCloud nunca sincroniza un texto a
    # medio escribir y nunca queda un .txt incompleto que nos haria
    # saltar el audio para siempre.
    salida = trabajo.salida
    salida.parent.mkdir(parents=True, exist_ok=True)
    temporal = salida.with_suffix(".txt.parcial")
    temporal.write_text(texto + "\n", encoding="utf-8")
    os.replace(temporal, salida)

    tardo = time.time() - empezado
    log.info(
        "Listo: %s  (%d caracteres, %.0f segundos)",
        salida.relative_to(DIARIO),
        len(texto),
        tardo,
    )


def una_vuelta(tamanos_previos: dict, fallos: dict) -> None:
    """Revisa las dos fuentes una vez y transcribe lo que este listo."""
    CARPETA_AUDIO.mkdir(parents=True, exist_ok=True)
    CARPETA_TEXTO.mkdir(parents=True, exist_ok=True)
    CARPETA_TEXTO_OTRAS.mkdir(parents=True, exist_ok=True)

    revisar_marcadores_de_icloud()

    pendientes = []
    try:
        pendientes += pendientes_de_notas_de_voz()
        _ya_avisado.discard("sin-permiso")
    except PermissionError:
        # macOS protege la carpeta de Notas de Voz. Hasta que se autorice,
        # avisamos una sola vez y seguimos vigilando la carpeta de respaldo.
        avisar_una_vez(
            "sin-permiso",
            "macOS no me deja leer Notas de Voz. Hay que dar 'Full Disk Access' "
            "al transcriptor: System Settings > Privacy & Security > Full Disk Access "
            "> (+) y elegir %s. Luego reinicia el transcriptor. "
            "Mientras tanto sigo vigilando la carpeta de Audio.",
            que_autorizar(),
        )
    except Exception as error:  # noqa: BLE001 - si la base de datos falla, seguimos con el respaldo
        log.error("No pude leer Notas de Voz esta vez: %s", error)
    pendientes += pendientes_de_carpeta_audio()

    for trabajo in pendientes:
        clave = str(trabajo.audio)
        if fallos.get(clave, 0) >= MAX_INTENTOS:
            continue
        if not esta_completo(trabajo.audio, tamanos_previos):
            continue

        try:
            transcribir(trabajo)
            fallos.pop(clave, None)
        except Exception as error:  # noqa: BLE001 - un audio malo no debe parar el resto
            fallos[clave] = fallos.get(clave, 0) + 1
            log.error(
                "Fallo al transcribir %s (intento %d de %d): %s",
                trabajo.etiqueta,
                fallos[clave],
                MAX_INTENTOS,
                error,
            )
            if fallos[clave] >= MAX_INTENTOS:
                log.error("Me rindo con %s hasta el proximo reinicio.", trabajo.etiqueta)


def main() -> None:
    configurar_log()
    asegurar_herramientas()

    solo_una_vez = "--una-vez" in sys.argv

    log.info("=" * 60)
    log.info("Transcriptor arrancando. Modelo: %s, idioma: %s", MODELO, IDIOMA)
    log.info("Vigilando Notas de Voz: %s", CARPETA_NOTAS_DE_VOZ)
    log.info("Vigilando (respaldo): %s", CARPETA_AUDIO)
    log.info("Guardando en: %s  (y en Otras/ lo que no sea Diario)", CARPETA_TEXTO)

    tamanos_previos: dict = {}
    fallos: dict = {}

    if solo_una_vez:
        # En modo prueba no esperamos a la segunda vuelta para confirmar
        # que el tamano es estable: medimos, dormimos un poco y medimos.
        una_vuelta(tamanos_previos, fallos)
        time.sleep(2)
        una_vuelta(tamanos_previos, fallos)
        log.info("Vuelta unica terminada.")
        return

    while True:
        try:
            una_vuelta(tamanos_previos, fallos)
        except Exception as error:  # noqa: BLE001 - el vigilante nunca se muere
            log.error("Error inesperado en la revision: %s", error)
        time.sleep(SEGUNDOS_ENTRE_REVISIONES)


if __name__ == "__main__":
    main()
