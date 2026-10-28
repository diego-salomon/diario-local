# Diario Local

The machinery behind my morning routine. I record a voice note on my iPhone, and a few minutes later it's text on my Mac. Every morning, one email arrives with what matters today plus a reading from the two books I'm working through.

Everything runs on my own Mac. Transcription never touches the internet, needs no API keys, and costs nothing.

## Transcriber (`transcriptor.py`)

- A launchd agent checks every 30 seconds for new recordings.
- It reads straight from the Voice Memos library. Titles aren't in the file names, so it reads them from the app's SQLite database, always from a temporary read-only copy. An iCloud folder works as a fallback source.
- It waits for iCloud to finish downloading, skips anything in "Recently Deleted", and never transcribes the same file twice.
- It runs **Whisper large-v3-turbo through MLX** on the Mac's GPU. About 30 seconds of audio takes 3 seconds, so a half-hour note is done in about a minute.
- The output has a timestamp every minute and paragraphs split at natural pauses, so I can jump back to the exact moment in the audio.
- The language is forced to Spanish on purpose. I tested auto-detection on a real note mixing Spanish and English, and Whisper decided it was English and returned a broken half-translation.
- Notes titled "Diario…" go to the journal. Everything else goes to a separate folder.

## Morning email (`morning-mail/`)

- At 9:45 a scheduled Claude task reads my calendar, my open tasks and the latest journal transcripts, and writes a short brief for the day.
- At 10:05 a local script adds the day's entry from my own copy of a daily-reader book (read straight from the EPUB) and sends one clean email from my Gmail to myself.

The books and the credentials stay on my machine. They aren't in this repo.

## Stack

Python · MLX Whisper · SQLite · launchd · Claude scheduled tasks · Gmail SMTP

The code comments are in Spanish.
