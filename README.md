# SoundCloud Downloader

A Flask-based web app to download SoundCloud playlists as ZIP files.

## Setup

1. Install Python 3.8+ if not already installed.
2. Run `start.bat` to install dependencies and start the server.
3. Open http://localhost:5000 in your browser or renderer.

## Manual Commands

If you prefer manual setup:

```bash
pip install -r requirements.txt
python app.py
```

## Features

- Fetch SoundCloud playlist metadata
- Download tracks as MP3 in a ZIP file
- Progress tracking for downloads
- Supports public playlists and user tracks

## Notes

- Large playlists may take time to fetch metadata (up to 2 minutes)
- Downloads run in the background with real-time progress