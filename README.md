# Diario local

Record a voice note on your iPhone and, a few minutes later, it's text on your Mac. Optionally, one email every morning with a brief of the day and a reading from a book.

Transcription runs on the Mac. No internet, no API keys, no cost.

## Transcriber (`transcriptor.py`)

Checks every 30 seconds for new recordings in the Voice Memos library (reading the titles from a read-only copy of its database), waits for iCloud to finish downloading, skips "Recently Deleted", and never transcribes the same note twice. It runs Whisper large-v3 through MLX on the GPU, with a timestamp every minute and paragraphs at your pauses. Notes titled "Diario…" go to `Transcripciones/`, the rest to `Transcripciones/Otras/`. The language is set to Spanish; change `IDIOMA` at the top of the file.

## Run it

Apple Silicon Mac, Python 3.12, FFmpeg (`brew install ffmpeg`).

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python transcriptor.py --una-vez     # transcribe what's new, once
```

The first run downloads the model (a few GB). Reading Voice Memos needs Full Disk Access (System Settings, Privacy & Security). To avoid giving it to every Python, copy your Python binary into a small `Transcriptor.app` and grant access to that one. `local.diario.transcriptor.plist.example` starts it at login with launchd: replace `YOUR_USER` and copy it to `~/Library/LaunchAgents/`.

Output goes to `~/Documents/Diario`. Set `DIARIO_DIR` to change it.

## Morning email (`morning-mail/`)

`correo-diario.py` sends one email from your Gmail to your Gmail with `brief-hoy.html` (any HTML you or an assistant write there each morning) and, if `LIBRO_EPUB` is set, the day's entry from a daily-reader EPUB. EPUBs are laid out differently, so adjust `OPENER` and `LIBRO_PATRON` to yours.

```bash
security add-generic-password -s diario-gmail -a you@gmail.com -w   # Gmail app password, kept in the Keychain
DIARIO_EMAIL=you@gmail.com python3 morning-mail/correo-diario.py
```

Schedule it with `morning-mail/local.diario.correo.plist.example`.

Code comments are in Spanish. MIT license.
