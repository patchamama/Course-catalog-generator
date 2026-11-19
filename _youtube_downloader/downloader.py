#!/usr/bin/env python3
"""
YouTube Video & Subtitle Downloader using yt-dlp
"""

import argparse
import os
import sys
import re
import subprocess
import yt_dlp
from pathlib import Path

# android works without a JS runtime and gives reliable results
EXTRACTOR_ARGS = {"youtube": {"player_client": ["android"]}}

DEFAULT_WANTED_LANGS = ['en', 'de', 'es']


def sanitize_filename(name: str) -> str:
    """Remove or replace characters that are invalid in filenames."""
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = re.sub(r'\s+', '_', name.strip())
    return name[:180]  # limit length


def build_output_template(info: dict) -> str:
    title = sanitize_filename(info.get('title', 'video'))
    video_id = info.get('id', 'unknown')
    return f"{title}_[{video_id}]"


# YouTube video IDs are exactly 11 chars: letters, digits, - and _
_VIDEO_ID_RE = re.compile(r'^[A-Za-z0-9_-]{11}$')
# Playlist IDs: PL / UU / FL / OL / RD followed by more chars
_PLAYLIST_ID_RE = re.compile(r'^(PL|UU|FL|OL|RD)[A-Za-z0-9_-]+$')
# Channel IDs start with UC
_CHANNEL_ID_RE = re.compile(r'^UC[A-Za-z0-9_-]{22}$')


def normalize_to_url(value: str) -> str:
    """Convert a bare YouTube ID to its canonical URL; return full URLs unchanged."""
    if value.startswith('http://') or value.startswith('https://'):
        return value
    if _PLAYLIST_ID_RE.match(value):
        return f'https://www.youtube.com/playlist?list={value}'
    if _CHANNEL_ID_RE.match(value):
        return f'https://www.youtube.com/channel/{value}'
    if _VIDEO_ID_RE.match(value):
        return f'https://www.youtube.com/watch?v={value}'
    return value  # return as-is; yt-dlp will report the error


def get_urls_from_file(filepath: str) -> list[str]:
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith('#')]
    return [normalize_to_url(line) for line in lines]


def available_subtitle_langs(info: dict, wanted_langs: list[str]) -> list[str]:
    """Return only the wanted languages that are available for this video."""
    available: set[str] = set()
    for key in ('subtitles', 'automatic_captions'):
        available.update(info.get(key, {}).keys())
    available.discard('live_chat')
    return [lang for lang in wanted_langs if lang in available]


_TIMESTAMP_RE = re.compile(r'(?m)^(\d{1,2}:\d{2}(?::\d{2})?)\s+(.+?)\s*$')


def parse_timestamps(text: str) -> list[tuple[int, str]]:
    """Extract (seconds, title) pairs from a description that contains a track index."""
    entries = []
    for m in _TIMESTAMP_RE.finditer(text):
        parts = m.group(1).split(':')
        secs = int(parts[0]) * 60 + int(parts[1]) if len(parts) == 2 \
            else int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        entries.append((secs, m.group(2).strip()))
    return entries


def secs_to_hms(s: int) -> str:
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _srt_timestamp_to_ms(value: str) -> int:
    h, m, rest = value.replace('.', ',').split(':')
    s, ms = rest.split(',')
    return ((int(h) * 3600 + int(m) * 60 + int(s)) * 1000) + int(ms.ljust(3, '0')[:3])


def _ms_to_srt_timestamp(value: int) -> str:
    value = max(0, int(value))
    ms = value % 1000
    total = value // 1000
    s = total % 60
    total //= 60
    m = total % 60
    h = total // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


_SRT_TIME_RE = re.compile(
    r'(?P<start>\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*'
    r'(?P<end>\d{2}:\d{2}:\d{2}[,.]\d{3})(?P<settings>[^\n\r]*)'
)


def split_srt_by_timestamps(
    subtitle_path: str,
    timestamps: list[tuple[int, str]],
    output_folder: str,
    duration: int | None,
) -> list[str]:
    """Create per-chunk SRT files clipped and shifted to each split media segment."""
    src = Path(subtitle_path)
    try:
        text = src.read_text(encoding='utf-8-sig')
    except UnicodeDecodeError:
        text = src.read_text(encoding='utf-8', errors='replace')

    cues: list[tuple[int, int, str, str]] = []
    for block in re.split(r'\n\s*\n', text.strip()):
        lines = block.strip().splitlines()
        if not lines:
            continue
        time_idx = next((i for i, line in enumerate(lines) if '-->' in line), -1)
        if time_idx < 0:
            continue
        m = _SRT_TIME_RE.search(lines[time_idx])
        if not m:
            continue
        cue_text = '\n'.join(lines[time_idx + 1:]).strip()
        if not cue_text:
            continue
        cues.append((
            _srt_timestamp_to_ms(m.group('start')),
            _srt_timestamp_to_ms(m.group('end')),
            m.group('settings') or '',
            cue_text,
        ))

    if not cues:
        return []

    created: list[str] = []
    stem = src.stem
    suffix = ''
    # Original files are normally <base>.<lang>.srt; keep just ".<lang>" for chunks.
    if '.' in stem:
        suffix = '.' + stem.rsplit('.', 1)[1]

    for i, (start_sec, title) in enumerate(timestamps):
        end_sec = timestamps[i + 1][0] if i + 1 < len(timestamps) else duration
        start_ms = start_sec * 1000
        end_ms = (end_sec * 1000) if end_sec is not None else None
        chunk_stem = f"{i + 1:02d} - {sanitize_filename(title)}"

        lines: list[str] = []
        cue_no = 1
        for cue_start, cue_end, settings, cue_text in cues:
            if cue_end <= start_ms:
                continue
            if end_ms is not None and cue_start >= end_ms:
                continue
            clipped_start = max(cue_start, start_ms) - start_ms
            clipped_end = (min(cue_end, end_ms) if end_ms is not None else cue_end) - start_ms
            if clipped_end <= clipped_start:
                continue
            lines.extend([
                str(cue_no),
                f"{_ms_to_srt_timestamp(clipped_start)} --> {_ms_to_srt_timestamp(clipped_end)}{settings}",
                cue_text,
                '',
            ])
            cue_no += 1

        if lines:
            out_path = os.path.join(output_folder, f"{chunk_stem}{suffix}.srt")
            Path(out_path).write_text('\n'.join(lines), encoding='utf-8')
            created.append(out_path)

    return created


def find_downloaded_subtitles(output_dir: str, base_name: str) -> list[str]:
    """Return downloaded SRT subtitle files for the original media base name."""
    base = base_name + '.'
    found: list[str] = []
    for item in os.listdir(output_dir):
        if item.startswith(base) and item.endswith('.srt'):
            found.append(os.path.join(output_dir, item))
    return sorted(found)


def split_media_by_timestamps(
    media_path: str,
    timestamps: list[tuple[int, str]],
    output_dir: str,
    folder_name: str,
    duration: int | None,
) -> list[str]:
    ext = os.path.splitext(media_path)[1]
    folder = os.path.join(output_dir, sanitize_filename(folder_name))
    os.makedirs(folder, exist_ok=True)
    print(f"\n  Splitting into {len(timestamps)} tracks → {os.path.basename(folder)}/")

    created: list[str] = []
    for i, (start, title) in enumerate(timestamps):
        end = timestamps[i + 1][0] if i + 1 < len(timestamps) else duration
        out_name = f"{i + 1:02d} - {sanitize_filename(title)}{ext}"
        out_path = os.path.join(folder, out_name)

        cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-i', media_path,
               '-ss', secs_to_hms(start)]
        if end is not None:
            cmd += ['-to', secs_to_hms(end)]
        cmd += ['-c', 'copy', out_path]

        print(f"    [{i + 1}/{len(timestamps)}] {out_name}")
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            print(f"  [ERROR] ffmpeg: {result.stderr.decode(errors='replace')[:300]}")
        else:
            created.append(out_path)

    print(f"  Split complete: {len(created)} tracks in {os.path.basename(folder)}/")
    return created


def extract_mp3_from_file(src: str, dst: str) -> bool:
    """Extract audio from src to dst (mp3). Returns True on success."""
    cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-i', src, '-q:a', '0', '-map', 'a', dst]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        print(f"  [ERROR] MP3 extraction: {result.stderr.decode(errors='replace')[:300]}")
        return False
    return True


def expand_playlist(url: str, cookies_from_browser: str | None, cookies_file: str | None) -> list[str]:
    """Return individual video URLs for a playlist/channel; return [url] for single videos."""
    opts: dict = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,   # don't fetch full info for each entry, just list them
        'ignoreerrors': True,
        'extractor_args': EXTRACTOR_ARGS,
    }
    if cookies_from_browser:
        opts['cookiesfrombrowser'] = (cookies_from_browser,)
    elif cookies_file:
        opts['cookiefile'] = cookies_file

    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError:
            return [url]

    if info is None:
        return [url]

    # Playlist / channel: entries list present
    entries = info.get('entries')
    if not entries:
        return [url]

    video_urls = []
    for entry in entries:
        if entry is None:
            continue
        vid_id = entry.get('id')
        if vid_id:
            video_urls.append(f'https://www.youtube.com/watch?v={vid_id}')
    return video_urls if video_urls else [url]


def build_ydl_opts(
    output_dir: str,
    download_video_file: bool,
    cookies_from_browser: str | None,
    cookies_file: str | None,
    wanted_langs: list[str] | None = None,
    download_description: bool = False,
    only_mp3: bool = False,
    progress_hook=None,
) -> dict:
    wanted_langs = wanted_langs or DEFAULT_WANTED_LANGS
    opts: dict = {
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': wanted_langs,
        'subtitlesformat': 'srt',
        'outtmpl': {'default': os.path.join(output_dir, '%(title)s_%(id)s.%(ext)s')},
        'quiet': False,
        'no_warnings': True,
        'noplaylist': True,
        'extractor_args': EXTRACTOR_ARGS,
        # Rate-limit protection
        'sleep_interval_subtitles': 2,
        'sleep_interval': 1,
        'sleep_interval_requests': 1,
        'retries': 5,
        'ignoreerrors': True,
    }

    if only_mp3:
        opts['format'] = 'bestaudio/best'
        opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    elif download_video_file:
        opts['format'] = 'bestvideo+bestaudio/best'
        opts['merge_output_format'] = 'mp4'
    else:
        opts['skip_download'] = True

    if download_description:
        opts['writedescription'] = True

    if cookies_from_browser:
        opts['cookiesfrombrowser'] = (cookies_from_browser,)
    elif cookies_file:
        opts['cookiefile'] = cookies_file

    if progress_hook is not None:
        opts['progress_hooks'] = [progress_hook]

    return opts


def download_video(
    url: str,
    output_dir: str,
    download_video_file: bool,
    cookies_from_browser: str | None,
    cookies_file: str | None,
    wanted_langs: list[str] | None = None,
    download_description: bool = False,
    split_video_by_timestamps: bool = False,
    split_mp3_by_timestamps: bool = False,
    extract_mp3: bool = False,
    only_mp3: bool = False,
    progress_hook=None,
) -> None:
    os.makedirs(output_dir, exist_ok=True)

    print(f"\n→ Processing: {url}")

    wanted_langs = wanted_langs or DEFAULT_WANTED_LANGS
    opts = build_ydl_opts(
        output_dir, download_video_file, cookies_from_browser, cookies_file,
        wanted_langs, download_description, only_mp3,
        progress_hook=progress_hook,
    )

    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as e:
            print(f"  [ERROR] Could not fetch info: {e}")
            return

        if info is None:
            print(f"  [ERROR] Could not fetch info (invalid URL or private video?): {url}")
            return

        base_name = build_output_template(info)
        out_path = os.path.join(output_dir, base_name)

        available_langs = available_subtitle_langs(info, wanted_langs)
        if available_langs:
            print(f"  Subtitles to download: {', '.join(available_langs)}")
        else:
            print(f"  No subtitles available in {wanted_langs} for this video.")

        # Narrow down to available langs and set final output path
        ydl.params['subtitleslangs'] = available_langs
        ydl.params['outtmpl'] = {'default': out_path + '.%(ext)s'}

        if available_langs or download_video_file or only_mp3:
            if only_mp3:
                action = "MP3"
            elif download_video_file:
                action = "video + subtitles"
            else:
                action = "subtitles"
            print(f"  Downloading {action} → {base_name}")
            try:
                ydl.process_ie_result(info, download=True)
            except yt_dlp.utils.DownloadError as e:
                print(f"  [ERROR] Download failed: {e}")
                return

    if not download_video_file and not only_mp3:
        placeholder = out_path + '.mp4'
        if not os.path.exists(placeholder):
            open(placeholder, 'w').close()
            print(f"  Created empty placeholder: {os.path.basename(placeholder)}")

    if download_description:
        desc_src = out_path + '.description'
        desc_dst = out_path + '.description.txt'
        if os.path.exists(desc_src):
            os.replace(desc_src, desc_dst)
            print(f"  Saved description as: {os.path.basename(desc_dst)}")

    # Extract MP3 from downloaded video when --video --mp3
    if extract_mp3 and download_video_file:
        mp3_file = out_path + '.mp3'
        print(f"  Extracting MP3 → {os.path.basename(mp3_file)}")
        extract_mp3_from_file(out_path + '.mp4', mp3_file)

    # Split by timestamps. MP3 and video are separate actions, but both reuse the
    # same folder named after the original output file stem.
    if split_video_by_timestamps or split_mp3_by_timestamps:
        description = info.get('description', '') or ''
        timestamps = parse_timestamps(description)
        if timestamps:
            split_folder = os.path.join(output_dir, base_name)

            if split_video_by_timestamps:
                media_file = out_path + '.mp4'
                if os.path.exists(media_file):
                    split_media_by_timestamps(
                        media_file, timestamps, output_dir, base_name, info.get('duration')
                    )
                else:
                    print(f"  [WARN] Video file not found for splitting: {media_file}")

            if split_mp3_by_timestamps:
                media_file = out_path + '.mp3'
                if os.path.exists(media_file):
                    split_media_by_timestamps(
                        media_file, timestamps, output_dir, base_name, info.get('duration')
                    )
                else:
                    print(f"  [WARN] MP3 file not found for splitting: {media_file}")

            subtitle_files = find_downloaded_subtitles(output_dir, base_name)
            if subtitle_files and os.path.isdir(split_folder):
                subtitle_count = 0
                for subtitle_file in subtitle_files:
                    subtitle_count += len(split_srt_by_timestamps(
                        subtitle_file, timestamps, split_folder, info.get('duration')
                    ))
                print(f"  Created synchronized chunk subtitles: {subtitle_count}")
            elif not subtitle_files:
                print("  No original .srt subtitles found for chunk subtitle generation.")
        else:
            print(f"  No timestamps found in description — skipping split.")

    print(f"  Done: {base_name}")


def ask_download_video() -> bool:
    while True:
        answer = input("\nDo you want to download the actual video file (.mp4)? [y/n]: ").strip().lower()
        if answer in ('y', 'yes'):
            return True
        if answer in ('n', 'no'):
            return False
        print("Please enter 'y' or 'n'.")


def ask_download_description() -> bool:
    while True:
        answer = input("Download video description to .description.txt file? [y/n]: ").strip().lower()
        if answer in ('y', 'yes'):
            return True
        if answer in ('n', 'no'):
            return False
        print("Please enter 'y' or 'n'.")


def ask_mp3(with_video: bool) -> bool:
    prompt = "Also extract MP3 from video? [y/n]: " if with_video \
        else "Download audio as MP3? [y/n]: "
    while True:
        answer = input(prompt).strip().lower()
        if answer in ('y', 'yes'):
            return True
        if answer in ('n', 'no'):
            return False
        print("Please enter 'y' or 'n'.")


def ask_split_by_timestamps(label: str) -> bool:
    while True:
        answer = input(f"Split {label} by timestamps found in description? [y/n]: ").strip().lower()
        if answer in ('y', 'yes'):
            return True
        if answer in ('n', 'no'):
            return False
        print("Please enter 'y' or 'n'.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Download YouTube videos and subtitles using yt-dlp.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python downloader.py https://www.youtube.com/watch?v=XXXX
  python downloader.py https://youtu.be/XXXX https://youtu.be/YYYY
  python downloader.py --file urls.txt
  python downloader.py --file urls.txt --video      (skip the prompt, always download video)
  python downloader.py --file urls.txt --no-video   (skip the prompt, never download video)
  python downloader.py URL --output ./downloads
  python downloader.py URL --cookies-from-browser chrome
  python downloader.py URL --cookies cookies.txt
        """,
    )
    parser.add_argument('urls', nargs='*', help='One or more YouTube URLs')
    parser.add_argument('--file', '-f', metavar='FILE',
                        help='Text file containing one YouTube URL per line')
    parser.add_argument('--output', '-o', metavar='DIR', default='downloads',
                        help='Output directory (default: ./downloads)')
    parser.add_argument('--video', action='store_true', default=None,
                        help='Download the video file (skip prompt)')
    parser.add_argument('--no-video', dest='video', action='store_false',
                        help='Skip video download, only get subtitles (skip prompt)')

    parser.add_argument('--description', action='store_true', default=None,
                        help='Save video description to .description.txt file (skip prompt)')
    parser.add_argument('--no-description', dest='description', action='store_false',
                        help='Skip description download (skip prompt)')

    parser.add_argument('--mp3', action='store_true', default=None,
                        help='Generate MP3: extracted from video if --video, else audio-only download')
    parser.add_argument('--no-mp3', dest='mp3', action='store_false',
                        help='Skip MP3 generation (skip prompt)')
    parser.add_argument('--only-mp3', action='store_true', default=False,
                        help='Download audio only as MP3, no video (implies --no-video --mp3)')

    parser.add_argument('--split', action='store_true', default=None,
                        help='Legacy: split downloaded media by timestamps in description (skip prompt)')
    parser.add_argument('--no-split', dest='split', action='store_false',
                        help='Skip timestamp splitting (skip prompt)')
    parser.add_argument('--split-video', action='store_true', default=None,
                        help='Split downloaded video by timestamps in description (skip prompt)')
    parser.add_argument('--no-split-video', dest='split_video', action='store_false',
                        help='Skip video timestamp splitting (skip prompt)')
    parser.add_argument('--split-mp3', action='store_true', default=None,
                        help='Split downloaded/generated MP3 by timestamps in description (skip prompt)')
    parser.add_argument('--no-split-mp3', dest='split_mp3', action='store_false',
                        help='Skip MP3 timestamp splitting (skip prompt)')

    cookie_group = parser.add_mutually_exclusive_group()
    cookie_group.add_argument(
        '--cookies-from-browser', metavar='BROWSER',
        help='Use cookies from browser to avoid 429 rate limits. '
             'Values: chrome, firefox, edge, safari, brave, chromium, opera',
    )
    cookie_group.add_argument(
        '--cookies', metavar='FILE',
        help='Path to a Netscape-format cookies.txt file',
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Collect all URLs — normalize bare IDs to full URLs
    all_urls: list[str] = [normalize_to_url(u) for u in args.urls]

    if args.file:
        if not os.path.isfile(args.file):
            print(f"[ERROR] File not found: {args.file}", file=sys.stderr)
            sys.exit(1)
        all_urls.extend(get_urls_from_file(args.file))

    if not all_urls:
        print("[ERROR] No URLs provided. Use positional args or --file.", file=sys.stderr)
        sys.exit(1)

    # Decide download mode (ask before expanding playlists)
    if args.only_mp3:
        download_video_file = False
        want_mp3 = True
    else:
        if args.video is None:
            download_video_file = ask_download_video()
        else:
            download_video_file = args.video

        if args.mp3 is None:
            want_mp3 = ask_mp3(with_video=download_video_file)
        else:
            want_mp3 = args.mp3

    if args.split_mp3:
        want_mp3 = True
    if args.split_video:
        download_video_file = True

    only_mp3 = want_mp3 and not download_video_file
    extract_mp3 = want_mp3 and download_video_file

    if args.description is None:
        download_description = ask_download_description()
    else:
        download_description = args.description

    if args.split is not None:
        split_video_by_timestamps = bool(args.split and download_video_file)
        split_mp3_by_timestamps = bool(args.split and want_mp3)
        if args.split and not (download_video_file or want_mp3):
            print("[WARN] --split requires video or MP3 download; ignoring.")
    else:
        if args.split_video is None:
            split_video_by_timestamps = ask_split_by_timestamps("video") if download_video_file else False
        else:
            split_video_by_timestamps = bool(args.split_video and download_video_file)
            if args.split_video and not download_video_file:
                print("[WARN] --split-video requires video download; ignoring.")

        if args.split_mp3 is None:
            split_mp3_by_timestamps = ask_split_by_timestamps("MP3") if want_mp3 else False
        else:
            split_mp3_by_timestamps = bool(args.split_mp3 and want_mp3)
            if args.split_mp3 and not want_mp3:
                print("[WARN] --split-mp3 requires MP3 generation/download; ignoring.")

    if only_mp3:
        action = "MP3 only"
    elif download_video_file and extract_mp3:
        action = "video + MP3 + subtitles"
    elif download_video_file:
        action = "video + subtitles"
    else:
        action = "subtitles only (empty .mp4 placeholder)"
    print(f"Mode: {action}")
    print(f"Output directory: {os.path.abspath(args.output)}")
    if args.cookies_from_browser:
        print(f"Cookies from: {args.cookies_from_browser}")
    elif args.cookies:
        print(f"Cookies file: {args.cookies}")
    else:
        print("Cookies: none (if you get 429 errors, use --cookies-from-browser chrome)")

    # Deduplicate preserving order
    seen: set[str] = set()
    unique_inputs = [u for u in all_urls if not (u in seen or seen.add(u))]

    # Expand playlists/channels into individual video URLs
    video_urls: list[str] = []
    for raw in unique_inputs:
        expanded = expand_playlist(raw, args.cookies_from_browser, args.cookies)
        if len(expanded) > 1:
            print(f"  Playlist expanded: {raw} → {len(expanded)} videos")
        video_urls.extend(expanded)

    # Deduplicate again after expansion
    seen2: set[str] = set()
    video_urls = [u for u in video_urls if not (u in seen2 or seen2.add(u))]

    print(f"\nTotal videos to process: {len(video_urls)}")

    # Process each video
    for i, url in enumerate(video_urls, 1):
        print(f"\n[{i}/{len(video_urls)}]")
        download_video(
            url, args.output, download_video_file,
            args.cookies_from_browser, args.cookies,
            DEFAULT_WANTED_LANGS,
            download_description, split_video_by_timestamps, split_mp3_by_timestamps,
            extract_mp3, only_mp3,
        )

    print("\nAll done!")


if __name__ == '__main__':
    main()
