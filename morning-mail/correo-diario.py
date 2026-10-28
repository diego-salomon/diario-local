#!/usr/bin/env python3
"""
Correo diario: arma y envía un solo correo por la mañana con
1) el brief del día que un asistente dejó en $DIARIO_DIR/brief-hoy.html
2) y, si hay un libro configurado, la lectura del día desde un EPUB.
Lo manda de tu Gmail a tu Gmail. Todo local; solo sale el correo a ti mismo.

Config (variables de entorno):
  DIARIO_EMAIL   tu dirección de Gmail (remitente y destinatario)
  DIARIO_DIR     carpeta del brief (por defecto ~/Documents/Diario)
  LIBRO_EPUB     opcional: ruta a un EPUB de lecturas diarias
  LIBRO_TITULO   opcional: el nombre que sale encima de la lectura
La contraseña de aplicación de Gmail va en el Llavero de macOS:
  security add-generic-password -s diario-gmail -a TU_CORREO -w
"""

import os
import re
import smtplib
import ssl
import sys
import zipfile
from datetime import date, datetime
from email.mime.text import MIMEText
from pathlib import Path

# ---------- config ----------
AQUI = Path(__file__).resolve().parent
EPUB = Path(os.environ["LIBRO_EPUB"]).expanduser() if os.environ.get("LIBRO_EPUB") else None
LIBRO_TITULO = os.environ.get("LIBRO_TITULO", "Lectura del día")
BRIEF = Path(os.environ.get("DIARIO_DIR", Path.home() / "Documents/Diario")).expanduser() / "brief-hoy.html"
CORREO = os.environ["DIARIO_EMAIL"]  # remitente y destinatario
# ----------------------------

# Cómo encontrar la lectura de hoy dentro del EPUB. Está hecho para un libro
# concreto (una entrada por día, el archivo c{n} donde n = OPENER[mes] + día).
# Para otro libro, abre el EPUB como zip y ajusta OPENER y NOMBRE_ARCHIVO.
NOMBRE_ARCHIVO = os.environ.get("LIBRO_PATRON", "OEBPS/c{n}.html")
OPENER = {1: 5, 2: 37, 3: 67, 4: 99, 5: 130, 6: 162,
          7: 193, 8: 225, 9: 257, 10: 288, 11: 320, 12: 351}
MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

ESTILO = ("margin:0 auto;max-width:560px;padding:0 20px;"
          "background:#ffffff;color:#24263f;"
          "font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;")
SERIF = "font-family:Georgia,'Times New Roman',serif;"
ROTULO = "font-size:13px;color:#5b62a6;margin:0;"
TITULO = SERIF + "font-size:24px;line-height:1.25;color:#24263f;margin:8px 0 0;"
PARRAFO = SERIF + "font-size:17px;line-height:1.75;color:#24263f;margin:14px 0 0;"
SEP = '<div style="height:1px;background:#e4d6d6;margin:36px 0 28px;line-height:1px;font-size:1px">&nbsp;</div>'


def lectura_de_hoy() -> str:
    """La lección del día de tu EPUB, con el estilo de las lecturas del correo."""
    hoy = date.today()
    n = OPENER[hoy.month] + hoy.day
    nombre = NOMBRE_ARCHIVO.format(n=n)
    with zipfile.ZipFile(EPUB) as z:
        html = z.read(nombre).decode("utf-8")
    cuerpo = re.search(r"<body[^>]*>(.*)</body>", html, re.S).group(1)
    cuerpo = re.sub(r"<img[^>]*>", "", cuerpo)
    cuerpo = re.sub(r"<a[^>]*>|</a>", "", cuerpo)
    salida = []
    for clase, texto in re.findall(r'<p[^>]*class="([^"]*)"[^>]*>(.*?)</p>', cuerpo, re.S):
        if clase == "h3":          # la fecha del libro: ya está arriba del correo
            continue
        if clase == "h2":          # título de la lección
            salida.append(f'<p style="{TITULO}">{texto.strip().capitalize()}</p>')
        else:
            salida.append(f'<p style="{PARRAFO}">{texto.strip()}</p>')
    return "".join(salida)


def brief_de_hoy():
    """Devuelve (html, asunto) si el brief es de hoy; si no, (None, None)."""
    if not BRIEF.exists():
        return None, None
    if datetime.fromtimestamp(BRIEF.stat().st_mtime).date() != date.today():
        return None, None
    html = BRIEF.read_text(encoding="utf-8")
    m = re.search(r"<!--ASUNTO:(.*?)-->", html)
    asunto = m.group(1).strip() if m else None
    return html, asunto


def armar_correo() -> MIMEText:
    hoy = date.today()
    fecha = f"{hoy.day} de {MESES[hoy.month]}"
    brief, asunto = brief_de_hoy()

    partes = []
    if brief:
        partes.append(brief)
    else:
        partes.append(
            f'<div style="{ESTILO}"><p style="padding-top:24px">Hoy no se generó el brief '
            f"(la Mac o el asistente no corrieron a tiempo). "
            f"</p></div>"
        )
        asunto = None

    if EPUB and EPUB.exists():
        partes.append(
            f'<div style="{ESTILO}">{SEP}'
            f'<p style="{ROTULO}">{LIBRO_TITULO}</p>'
            f"{lectura_de_hoy()}</div>"
        )

    html = ('<meta name="color-scheme" content="light only">'
            '<meta name="supported-color-schemes" content="light">'
            f'<div style="background:#ffffff;color-scheme:light only">{"".join(partes)}</div>')
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = asunto or f"Brief · {fecha}"
    msg["From"] = CORREO
    msg["To"] = CORREO
    return msg


def enviar(msg: MIMEText) -> None:
    import subprocess
    password = subprocess.run(
        ["/usr/bin/security", "find-generic-password", "-s", "diario-gmail", "-a", CORREO, "-w"],
        capture_output=True, text=True, check=True).stdout.strip()
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
        s.login(CORREO, password)
        s.send_message(msg)


if __name__ == "__main__":
    try:
        enviar(armar_correo())
        print("enviado")
    except Exception as e:
        (AQUI / "correo_error.log").write_text(f"{datetime.now()}: {e}\n")
        sys.exit(1)
