# YouTube Downloader Web App

A local-first YouTube downloader with a bilingual web interface for downloading, organizing, previewing, and exporting videos, MP3 audio, subtitles, thumbnails, and placeholder-only video cards.

The project combines a Python `yt-dlp` backend with a single-page HTML frontend. It is designed for personal media organization workflows where downloaded content is grouped by video, tagged by topic, and reused instead of downloaded again.

## What this project does

- Downloads YouTube videos as MP4 files.
- Downloads subtitles in selected languages.
- Extracts or downloads MP3 audio.
- Creates empty `.mp4` placeholders for subtitle-only cards.
- Expands playlists and channels into individual video downloads.
- Organizes downloads by topic/category folders.
- Shows downloaded content as grouped video cards in a browser UI.
- Lets you preview local video/audio or open YouTube embeds.
- Supports bulk actions on selected cards.
- Copies selected content into temporary bundle folders.
- Optionally creates ZIP downloads for exportable content.
- Avoids re-downloading content that already exists locally.

## Quick start

### macOS / Linux / WSL

```bash
./start.sh
```

### Windows

```bat
start.bat
```

The launcher will:

1. Create `venv/` if it does not exist.
2. Install Python dependencies from `requirements.txt`.
3. Pick a free local port.
4. Open the web app in your browser.
5. Start the backend server.

## Requirements

| Requirement | Purpose |
|---|---|
| Python 3.10+ | Runs the backend and CLI downloader. |
| `yt-dlp` | Downloads YouTube media and metadata. |
| `ffmpeg` | Merges video/audio, extracts MP3, and splits media by timestamps. |
| A modern browser | Runs the local web UI. |

Install `ffmpeg` separately through your operating system package manager.

Examples:

```bash
# Ubuntu / Debian
sudo apt install ffmpeg

# macOS with Homebrew
brew install ffmpeg

# Windows with winget
winget install Gyan.FFmpeg
```

## Web interface features

### Bilingual UI

The interface can switch between:

- English
- Spanish

The language preference is saved in the browser.

### Subtitle languages

The subtitle selector is intentionally limited to these languages:

| Code | Language |
|---|---|
| EN | English |
| ES | Spanish |
| DE | German |
| FR | French |
| IT | Italian |
| PT | Portuguese |

### Download options

| Option | Description |
|---|---|
| Download video (`.mp4`) | Downloads the actual video file. |
| Extract MP3 | Creates an MP3 from the downloaded video. |
| MP3 only | Downloads audio only as MP3 without keeping video. |
| Description | Saves the video description when available. |
| Split by description index MP3 | Splits MP3/audio using timestamps found in the description, into a folder named after the original file. |
| Split by description index video | Splits video using timestamps found in the description, into a folder named after the original file. |
| Empty `.mp4` placeholder | Creates an empty video placeholder for subtitle-only cards. |

### Card view

Downloaded files are grouped by YouTube video ID. Each card can show:

- Video preview or YouTube thumbnail.
- Local MP3 playback.
- Subtitle language chips.
- Topic/category label.
- File type badges.
- File size and duration metadata where available.

### Filters and topics

The file view supports:

- Text search by title or video ID.
- Topic/category filtering.
- Assigning a topic to selected visible cards.

When a topic is assigned, the backend moves the matching files into that topic folder and updates local metadata.

### Bulk actions

Bulk actions apply only to selected cards. If no card is selected, the UI asks you to select at least one item.

Available actions:

| Action | Behavior |
|---|---|
| Videos | Copies existing MP4 content into a bundle folder; downloads missing videos if needed. MP4 video content is not zipped. |
| MP3 | Copies existing MP3 files into a bundle folder; downloads missing MP3 files if needed. Can be zipped. |
| Subtitles | Copies all existing subtitles for each selected card, regardless of language; downloads missing subtitles only when a selected card has none. Can be zipped. |
| No content | Copies empty `.mp4` placeholder files into a bundle folder. Can be zipped. |
| Delete | Deletes all local files for the selected video IDs. |

If `Zip` is disabled, the backend opens the output bundle folder in the system file manager when the action finishes.

If `Zip` is enabled, the backend creates a `.zip` file and the browser downloads it instead of opening the folder. MP4 video content is excluded from ZIP behavior by design.

## Output structure

Downloaded and generated content is stored under `downloads/`.

Typical layout:

```text
downloads/
  German/
    My_Title_[VIDEO_ID].mp4
    My_Title_[VIDEO_ID].en.srt
    My_Title_[VIDEO_ID].de.srt
    My_Title_[VIDEO_ID].mp3
  _bundles/
    subtitulos_20260520-092948/
      My_Title_[VIDEO_ID].en.srt
      My_Title_[VIDEO_ID].de.srt
    subtitulos_20260520-092948.zip
```

`downloads/` is ignored by Git because it contains local/generated media.

## Command-line usage

The web app is the main workflow, but `downloader.py` can also be used directly.

### Single URL

```bash
python downloader.py https://www.youtube.com/watch?v=VIDEO_ID
```

### Multiple URLs

```bash
python downloader.py https://youtu.be/AAA https://youtu.be/BBB
```

### From a file

Create `urls.txt`:

```text
# Lines starting with # are ignored
https://www.youtube.com/watch?v=VIDEO_ID_1
https://www.youtube.com/watch?v=VIDEO_ID_2
```

Run:

```bash
python downloader.py --file urls.txt
```

### Common CLI flags

| Flag | Description |
|---|---|
| `--file`, `-f` | Read URLs from a text file. |
| `--output`, `-o` | Choose an output directory. Defaults to `downloads/`. |
| `--video` | Download video without asking interactively. |
| `--no-video` | Skip video and download subtitles/placeholders only. |
| `--description` | Save video descriptions. |
| `--mp3` | Generate MP3 audio. |
| `--only-mp3` | Download audio as MP3 only. |
| `--split` | Legacy shortcut: split downloaded media by timestamps found in the description. |
| `--split-mp3` | Split generated/downloaded MP3 by timestamps found in the description. |
| `--split-video` | Split downloaded video by timestamps found in the description. |
| `--cookies-from-browser` | Load cookies from a browser profile. |
| `--cookies` | Use a Netscape-format cookies file. |

## API overview

The web frontend talks to `webapp.py` through local HTTP endpoints.

| Endpoint | Purpose |
|---|---|
| `GET /api/files` | Lists downloaded files and card metadata. |
| `GET /api/topics` | Lists known topics/categories. |
| `GET /api/events` | Streams job progress through Server-Sent Events. |
| `POST /api/download` | Starts a download job from URLs or IDs. |
| `POST /api/fetch-missing` | Downloads a missing content type for one video. |
| `POST /api/collect-content` | Performs selected bulk actions and creates bundle folders/ZIPs. |
| `POST /api/assign-topic` | Assigns a topic to selected videos and moves files. |
| `POST /api/delete` | Deletes all local files for selected videos. |

## Development notes

- The frontend is a single `index.html` file with embedded CSS and JavaScript.
- The backend is a lightweight Python `http.server` app in `webapp.py`.
- `downloader.py` contains the reusable `yt-dlp` download logic.
- The app version lives in `VERSION`, is exposed by `GET /api/version`, and is shown in the header.
- Before every commit + push, increment the version in `VERSION`.
- Progress updates use Server-Sent Events rather than WebSockets.
- Heavy local/generated folders are ignored by Git.

## Git ignore policy

The repository intentionally does not track:

- Virtual environments (`venv/`, `.venv/`).
- Downloaded media (`downloads/`).
- Python caches (`__pycache__/`, `*.pyc`).
- Local cookies, secrets, logs, temp files, and editor metadata.
- Local agent/tooling state.

## Troubleshooting

### YouTube returns rate-limit or sign-in errors

Try using cookies:

```bash
python downloader.py URL --cookies-from-browser chrome
```

Or paste/upload a Netscape-format `cookies.txt` file in the web UI.

### MP3 extraction or video merge fails

Verify that `ffmpeg` is installed and available in your `PATH`:

```bash
ffmpeg -version
```

### The browser does not open automatically

The launcher prints the local URL. Copy it into your browser manually.

## License

No license file is currently included. Add one before distributing or publishing the project.
