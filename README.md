# Diario local

The machinery behind my mornings. I record a voice note on my iPhone, and a few minutes later it's text on my Mac. Every morning one email arrives with what matters that day and a short reading.

Transcription happens on the Mac itself. Nothing goes up to the internet, there are no API keys, and it costs nothing.

## The transcriber (`transcriptor.py`)

A small background process checks every 30 seconds for new recordings. It reads them straight from the Voice Memos library. File names don't carry the titles, so it reads those from the app's database, always from a temporary read-only copy. It waits for iCloud to finish downloading, skips anything in "Recently Deleted", and never transcribes the same note twice.

It runs Whisper large-v3 through MLX on the Mac's GPU. Each minute of the transcript gets a timestamp, and paragraphs break where you pause, so you can find any moment in the audio again. Notes titled "Diario…" go to the journal folder, and everything else goes to a folder called `Otras`.

The language is set to Spanish on purpose. I tested auto-detect on a real note that mixed Spanish and English. Whisper decided it was English and returned a broken half-translation. Forcing Spanish fixes that and keeps English words as they were said.

## The morning email (`morning-mail/`)

At 9:45 a scheduled Claude task reads my calendar, my open tasks and my latest journal entries, and writes a short brief for the day into `brief-hoy.html`. At 10:05 `correo-diario.py` adds the day's entry from my copy of a daily-reader book, read straight from the EPUB, and sends everything as one email from my Gmail to my Gmail.

The book and the password aren't in this repo. The `OPENER` table maps each month to the book's internal files, so for another book you'd need to redo it.

## Run it

You need a Mac with Apple Silicon, FFmpeg (`brew install ffmpeg`) and Python 3.12.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python transcriptor.py --una-vez     # transcribe whatever is new, once
```

The first run downloads the Whisper model, which takes a few GB. To read the Voice Memos library, the Python that runs the script needs Full Disk Access (System Settings → Privacy & Security). I didn't want to give that to every Python on the machine, so I copied the interpreter into a tiny `Transcriptor.app` and gave access only to that. `com.diego.transcriptor.plist.example` shows how launchd starts it at login. Swap `YOUR_USER` for your user name and copy it to `~/Library/LaunchAgents/`.

Everything lands in `~/Documents/Personal/Diario`. Set `DIARIO_DIR` to use another folder.

For the email: put your EPUB as `morning-mail/maxwell.epub`, a [Gmail app password](https://myaccount.google.com/apppasswords) in `morning-mail/maxwell_password.txt`, set `DIARIO_EMAIL` to your address, and schedule it with the second plist. A plain-text password file isn't ideal. Moving it to the Keychain, like [luz-monitor](https://github.com/diego-salomon/luz-monitor) does, is next on my list.

The code comments are in Spanish.
