#!/usr/bin/env python3
import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlparse, unquote, parse_qs
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.request import urlopen

import downloader

ROOT = Path(__file__).resolve().parent
DOWNLOADS_DIR = ROOT / "downloads"
INDEX_FILE = ROOT / "index.html"
FAVICON_FILE = ROOT / "favicon.svg"
VERSION_FILE = ROOT / "VERSION"
META_FILE = DOWNLOADS_DIR / ".meta.json"
BUNDLES_DIRNAME = "_bundles"

# ───── SSE job registry ─────
JOBS: dict[str, queue.Queue] = {}
JOBS_LOCK = threading.Lock()


def _job_q(job_id: str) -> queue.Queue:
    with JOBS_LOCK:
        q = JOBS.get(job_id)
        if q is None:
            q = queue.Queue()
            JOBS[job_id] = q
        return q


def _job_close(job_id: str) -> None:
    with JOBS_LOCK:
        JOBS.pop(job_id, None)


def emit(job_id: str | None, event: str, data: dict) -> None:
    if not job_id:
        return
    _job_q(job_id).put((event, data))


def app_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except FileNotFoundError:
        return "0.0.0"


def _sanitize_topic(topic: str) -> str:
    bad = '<>:"/\\|?*\0\r\n\t'
    out = ''.join(c if c not in bad else '_' for c in (topic or '')).strip().strip('.')
    return out[:120]


def _target_dir_for_topic(topic: str) -> Path:
    safe = _sanitize_topic(topic)
    if not safe:
        return DOWNLOADS_DIR
    p = DOWNLOADS_DIR / safe
    p.mkdir(parents=True, exist_ok=True)
    return p


def make_progress_hook(job_id: str | None, url: str):
    state = {"last_pct": -1, "last_t": 0.0}

    def hook(d):
        if not job_id:
            return
        st = d.get('status')
        if st == 'downloading':
            tb = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            db = d.get('downloaded_bytes') or 0
            pct = int(db * 100 / tb) if tb else 0
            now = time.time()
            if pct != state["last_pct"] or now - state["last_t"] > 0.5:
                state["last_pct"] = pct
                state["last_t"] = now
                emit(job_id, "progress", {
                    "url": url, "percent": pct,
                    "downloaded": db, "total": tb,
                    "speed": d.get('speed') or 0,
                    "eta": d.get('eta') or 0,
                    "filename": os.path.basename(d.get('filename', '') or ''),
                })
        elif st == 'finished':
            emit(job_id, "progress", {
                "url": url, "percent": 100,
                "filename": os.path.basename(d.get('filename', '') or ''),
            })

    return hook


def parse_input_lines(raw: str) -> list[str]:
    lines = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(downloader.normalize_to_url(line))
    # dedupe preserving order
    seen = set()
    out = []
    for x in lines:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


_SUBTITLE_LANG_RE = None
_DURATION_CACHE: dict[str, float | None] = {}


def _subtitle_lang(name: str) -> str | None:
    import re
    global _SUBTITLE_LANG_RE
    if _SUBTITLE_LANG_RE is None:
        _SUBTITLE_LANG_RE = re.compile(r"\.([a-z]{2,3}(?:-[A-Za-z]{2,4})?)\.(?:srt|vtt|ass)$", re.IGNORECASE)
    m = _SUBTITLE_LANG_RE.search(name or "")
    return m.group(1).lower() if m else None


def _count_srt_chunks(path: Path) -> int | None:
    try:
        if path.suffix.lower() != ".srt":
            return None
        if path.stat().st_size > 5 * 1024 * 1024:
            return None
        text = path.read_text(encoding="utf-8", errors="ignore")
        import re
        n = sum(1 for _ in re.finditer(r"\n\s*\d+\s*\n\d{2}:\d{2}:\d{2}", "\n" + text))
        return n if n > 0 else None
    except Exception:
        return None


def _video_duration(path: Path) -> float | None:
    key = str(path)
    if key in _DURATION_CACHE:
        return _DURATION_CACHE[key]
    import shutil, subprocess
    ffprobe = shutil.which("ffprobe")
    duration: float | None = None
    if ffprobe:
        try:
            r = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, timeout=8,
            )
            v = (r.stdout or "").strip()
            if v:
                duration = float(v)
        except Exception:
            duration = None
    _DURATION_CACHE[key] = duration
    return duration


def _clean_title(name: str) -> str:
    import re
    base = re.sub(r"\.(srt|vtt|ass|mp4|mkv|webm|mp3|m4a|wav|jpg|jpeg|png)$", "", name, flags=re.IGNORECASE)
    base = re.sub(r"\.[a-z]{2,3}(?:-[A-Za-z]{2,4})?$", "", base, flags=re.IGNORECASE)
    base = re.sub(r"\s*\[[A-Za-z0-9_-]{11}\]\s*$", "", base)
    base = base.replace("_", " ").strip()
    return base


def list_files(include_details: bool = True):
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    meta = load_meta()
    items = []
    for p in sorted(DOWNLOADS_DIR.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(DOWNLOADS_DIR).as_posix()
        if rel == ".meta.json" or rel.split("/", 1)[0] == BUNDLES_DIRNAME:
            continue
        ext = p.suffix.lower()
        kind = "other"
        if ext in {".mp4", ".webm", ".mkv"}:
            kind = "video"
        elif ext in {".srt", ".vtt", ".ass"}:
            kind = "subtitle"
        elif ext in {".mp3", ".m4a", ".wav"}:
            kind = "audio"
        elif ext in {".jpg", ".jpeg", ".png", ".webp"}:
            kind = "image"
        size = p.stat().st_size
        item = {
            "name": p.name,
            "path": rel,
            "size": size,
            "mtime": p.stat().st_mtime,
            "kind": kind,
            "video_id": extract_video_id(p.name) or extract_video_id(rel),
            "topic": None,
            "url": f"/media/{rel}",
            "title": _clean_title(p.name),
            "is_placeholder": kind == "video" and size == 0,
        }
        if kind == "subtitle":
            item["lang"] = _subtitle_lang(p.name)
            if include_details:
                item["chunks"] = _count_srt_chunks(p)
        if include_details and kind == "video" and size > 0:
            item["duration"] = _video_duration(p)
        items.append(item)
    meta_topics = meta.get("video_topics", {})
    for it in items:
        # Topic priority: parent folder > meta.json
        parts = it["path"].split("/")
        if len(parts) > 1:
            it["topic"] = parts[0]
        else:
            vid = it.get("video_id")
            if vid:
                it["topic"] = meta_topics.get(vid)
    return items


def extract_video_id(text: str) -> str | None:
    import re
    m = re.search(r"\[([A-Za-z0-9_-]{11})\]", text or "")
    return m.group(1) if m else None


def load_meta() -> dict:
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"video_topics": {}}


def save_meta(meta: dict) -> None:
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    META_FILE.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _delete_video_files(video_id: str) -> list[str]:
    removed: list[str] = []
    if not video_id or len(video_id) != 11:
        return removed
    for p in list(DOWNLOADS_DIR.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(DOWNLOADS_DIR).as_posix()
        if rel.split("/", 1)[0] == BUNDLES_DIRNAME:
            continue
        if extract_video_id(p.name) == video_id:
            try:
                p.unlink()
                removed.append(rel)
            except Exception:
                pass
    # cleanup empty topic dirs (only direct children of DOWNLOADS_DIR)
    for sub in DOWNLOADS_DIR.iterdir():
        if sub.is_dir():
            try:
                if not any(sub.iterdir()):
                    sub.rmdir()
            except Exception:
                pass
    meta = load_meta()
    if video_id in meta.get("video_topics", {}):
        meta["video_topics"].pop(video_id, None)
        save_meta(meta)
    return removed


def _cleanup_empty_topic_dirs() -> None:
    for sub in DOWNLOADS_DIR.iterdir():
        if sub.is_dir() and sub.name != BUNDLES_DIRNAME:
            try:
                if not any(sub.iterdir()):
                    sub.rmdir()
            except Exception:
                pass


def _move_unique(src: Path, dst_dir: Path) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    if dst.exists() and dst.resolve() != src.resolve():
        stem, suffix = src.stem, src.suffix
        i = 2
        while True:
            candidate = dst_dir / f"{stem}_{i}{suffix}"
            if not candidate.exists():
                dst = candidate
                break
            i += 1
    if dst.resolve() != src.resolve():
        shutil.move(str(src), str(dst))
    return dst


def _assign_topic_to_videos(video_ids: list[str], topic: str) -> dict:
    topic = (topic or "").strip()
    if not topic:
        return {"ok": False, "error": "Tema requerido", "moved": [], "invalid_ids": video_ids}
    safe_topic = _sanitize_topic(topic)
    if not safe_topic:
        return {"ok": False, "error": "Tema inválido", "moved": [], "invalid_ids": video_ids}

    target_dir = _target_dir_for_topic(topic)
    valid = [vid for vid in dict.fromkeys(video_ids) if len(vid) == 11]
    invalid = [vid for vid in video_ids if len(vid) != 11]
    moved: list[dict] = []

    for p in list(DOWNLOADS_DIR.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(DOWNLOADS_DIR).as_posix()
        if rel == ".meta.json" or rel.split("/", 1)[0] == BUNDLES_DIRNAME:
            continue
        vid = extract_video_id(p.name) or extract_video_id(rel)
        if vid in valid:
            new_path = _move_unique(p, target_dir)
            moved.append({
                "video_id": vid,
                "from": rel,
                "to": new_path.relative_to(DOWNLOADS_DIR).as_posix(),
            })

    meta = load_meta()
    for vid in valid:
        meta.setdefault("video_topics", {})[vid] = safe_topic
    save_meta(meta)
    _cleanup_empty_topic_dirs()
    return {"ok": not invalid, "topic": safe_topic, "moved": moved, "count": len(moved), "invalid_ids": invalid}


def _existing_files_for_video(video_id: str, files: list[dict]) -> list[dict]:
    return [f for f in files if f.get("video_id") == video_id]


def _files_for_action(video_id: str, action: str, files: list[dict]) -> list[dict]:
    matches = _existing_files_for_video(video_id, files)
    if action == "subtitles":
        return [f for f in matches if f.get("kind") == "subtitle"]
    if action == "mp3":
        return [f for f in matches if f.get("kind") == "audio"]
    if action == "video":
        return [f for f in matches if f.get("kind") == "video" and not f.get("is_placeholder")]
    if action == "placeholder":
        return [f for f in matches if f.get("kind") == "video" and f.get("is_placeholder")]
    return []


def _index_files_by_video(files: list[dict]) -> dict[str, list[dict]]:
    by_video: dict[str, list[dict]] = {}
    for f in files:
        vid = f.get("video_id")
        if vid:
            by_video.setdefault(vid, []).append(f)
    return by_video


def _files_for_action_from_index(video_id: str, action: str, by_video: dict[str, list[dict]]) -> list[dict]:
    return _files_for_action(video_id, action, by_video.get(video_id, []))


def _resolve_topic_for_video(video_id: str, explicit_topic: str, files: list[dict]) -> str:
    if explicit_topic:
        return explicit_topic
    for f in files:
        if f.get("video_id") == video_id and f.get("topic"):
            return f["topic"]
    return ""


def _copy_unique(src: Path, dst_dir: Path) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    if dst.exists():
        try:
            if dst.stat().st_size == src.stat().st_size:
                return dst
        except Exception:
            pass
        stem, suffix = src.stem, src.suffix
        i = 2
        while True:
            candidate = dst_dir / f"{stem}_{i}{suffix}"
            if not candidate.exists():
                dst = candidate
                break
            i += 1
    shutil.copy2(src, dst)
    return dst


def _open_folder(path: Path) -> bool:
    """Best-effort folder reveal for the local desktop environment."""
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
            return True
        if "microsoft" in (Path("/proc/version").read_text(errors="ignore").lower() if Path("/proc/version").exists() else ""):
            r = subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True, timeout=3)
            win_path = (r.stdout or "").strip() or str(path)
            subprocess.Popen(["explorer.exe", win_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _zip_folder(folder: Path) -> Path:
    zip_path = folder.with_suffix(".zip")
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(folder).as_posix())
    return zip_path


def _run_collect_content(payload: dict, job_id: str | None) -> None:
    try:
        ids = [str(x).strip() for x in (payload.get("video_ids") or []) if str(x).strip()]
        action = (payload.get("action") or "").strip()
        topic = (payload.get("topic") or "").strip()
        langs = payload.get("langs") or ["en", "de", "es"]
        compress = bool(payload.get("compress", False))

        if action not in {"subtitles", "mp3", "video", "placeholder"}:
            emit(job_id, "complete", {"ok": False, "errors": ["action inválida"]})
            return
        ids = [vid for vid in dict.fromkeys(ids) if len(vid) == 11]
        if not ids:
            emit(job_id, "complete", {"ok": False, "errors": ["No hay IDs válidos"]})
            return

        label = {
            "subtitles": "subtitulos",
            "mp3": "mp3",
            "video": "videos",
            "placeholder": "videos_sin_contenido",
        }[action]
        stamp = time.strftime("%Y%m%d-%H%M%S")
        bundle_dir = DOWNLOADS_DIR / BUNDLES_DIRNAME / f"{label}_{stamp}"
        bundle_dir.mkdir(parents=True, exist_ok=True)

        emit(job_id, "start", {"total": len(ids), "action": action, "compress": compress})
        emit(job_id, "phase", {"phase": "indexing", "message": f"Indexando archivos existentes..."})
        current = list_files(include_details=False)
        by_video = _index_files_by_video(current)
        errors: list[str] = []
        copied: list[str] = []
        skipped = 0

        for i, video_id in enumerate(ids, 1):
            url = f"https://www.youtube.com/watch?v={video_id}"
            emit(job_id, "item_start", {"url": url, "index": i, "total": len(ids), "action": action})
            try:
                matches = _files_for_action_from_index(video_id, action, by_video)

                # Subtitles are language-agnostic here: if any subtitle exists, reuse all subtitles.
                # Other actions also reuse existing files and only hit yt-dlp when the selected card lacks that type.
                if not matches and action in {"subtitles", "mp3", "video"}:
                    target_dir = _target_dir_for_topic(_resolve_topic_for_video(video_id, topic, by_video.get(video_id, [])))
                    emit(job_id, "phase", {"phase": "downloading", "message": f"Descargando faltante {i}/{len(ids)}..."})
                    if action == "mp3":
                        downloader.download_video(
                            url, str(target_dir), False, None, None, langs, False, False, False, True,
                            progress_hook=make_progress_hook(job_id, url),
                        )
                    elif action == "video":
                        downloader.download_video(
                            url, str(target_dir), True, None, None, langs, False, False, False, False,
                            progress_hook=make_progress_hook(job_id, url),
                        )
                    else:
                        downloader.download_video(
                            url, str(target_dir), False, None, None, langs, False, False, False, False,
                            progress_hook=make_progress_hook(job_id, url),
                        )
                    current = list_files(include_details=False)
                    by_video = _index_files_by_video(current)
                    matches = _files_for_action_from_index(video_id, action, by_video)

                if not matches:
                    skipped += 1
                for f in matches:
                    src = DOWNLOADS_DIR / f["path"]
                    if src.exists() and src.is_file():
                        dst = _copy_unique(src, bundle_dir)
                        copied.append(dst.relative_to(DOWNLOADS_DIR).as_posix())
                emit(job_id, "item_done", {"url": url, "index": i, "total": len(ids), "ok": True, "action": action})
            except Exception as e:
                errors.append(f"{video_id}: {e}")
                emit(job_id, "item_done", {"url": url, "index": i, "total": len(ids), "ok": False, "error": str(e), "action": action})

        archive_rel = None
        archive_url = None
        if compress and copied:
            emit(job_id, "phase", {"phase": "zipping", "message": "Comprimiendo ZIP..."})
            zip_path = _zip_folder(bundle_dir)
            archive_rel = zip_path.relative_to(DOWNLOADS_DIR).as_posix()
            archive_url = f"/media/{archive_rel}"

        if not compress:
            emit(job_id, "phase", {"phase": "opening", "message": "Abriendo carpeta..."})
        folder_opened = False if compress else _open_folder(bundle_dir)
        emit(job_id, "complete", {
            "ok": len(errors) == 0,
            "errors": errors,
            "copied": copied,
            "copied_count": len(copied),
            "skipped": skipped,
            "folder": bundle_dir.relative_to(DOWNLOADS_DIR).as_posix(),
            "folder_opened": folder_opened,
            "archive": archive_rel,
            "archive_url": archive_url,
        })
    except Exception as e:
        emit(job_id, "complete", {"ok": False, "errors": [str(e)]})


def _run_download(payload: dict, job_id: str | None) -> None:
    try:
        urls_raw = payload.get("urls", "")
        urls = parse_input_lines(urls_raw)
        if not urls:
            emit(job_id, "complete", {"ok": False, "errors": [{"error": "No hay URLs/IDs válidos"}]})
            return

        selected_langs = payload.get("langs") or ["en", "de", "es"]
        selected_langs = [str(x).strip() for x in selected_langs if str(x).strip()] or ["en", "de", "es"]

        download_video_file = bool(payload.get("download_video", True))
        subtitle_only_placeholder = bool(payload.get("subtitle_only_placeholder", True))
        want_mp3 = bool(payload.get("mp3", False))
        only_mp3 = bool(payload.get("only_mp3", False))
        split_video_by_timestamps = bool(payload.get("split_video", payload.get("split", False)))
        split_mp3_by_timestamps = bool(payload.get("split_mp3", False))
        download_description = bool(payload.get("description", False))

        if only_mp3:
            download_video_file = False
            want_mp3 = True
        if split_video_by_timestamps:
            download_video_file = True
            only_mp3 = False
        if split_mp3_by_timestamps:
            want_mp3 = True
            if not download_video_file:
                only_mp3 = True
        extract_mp3 = want_mp3 and download_video_file

        cookies_from_browser = (payload.get("cookies_from_browser") or "").strip() or None
        cookie_text = (payload.get("cookies_text") or "").strip()
        cookie_file = None
        temp_cookie_path = None
        if not cookies_from_browser and cookie_text:
            fd, temp_path = tempfile.mkstemp(prefix="yt_cookies_", suffix=".txt")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(cookie_text)
            temp_cookie_path = temp_path
            cookie_file = temp_path

        DOWNLOADS_DIR.mkdir(exist_ok=True)
        topic = (payload.get("topic") or "").strip()
        target_dir = _target_dir_for_topic(topic)
        before = {f["path"] for f in list_files()}

        emit(job_id, "phase", {"phase": "expanding", "message": f"Expandiendo {len(urls)} entradas..."})
        expanded = []
        for u in urls:
            expanded.extend(downloader.expand_playlist(u, cookies_from_browser, cookie_file))
        seen, final_urls = set(), []
        for u in expanded:
            if u not in seen:
                seen.add(u)
                final_urls.append(u)

        total = len(final_urls)
        emit(job_id, "start", {"total": total, "langs": selected_langs, "topic": topic})

        errors = []
        for i, u in enumerate(final_urls, 1):
            emit(job_id, "item_start", {"url": u, "index": i, "total": total})
            try:
                downloader.download_video(
                    u, str(target_dir), download_video_file,
                    cookies_from_browser, cookie_file, selected_langs,
                    download_description, split_video_by_timestamps, split_mp3_by_timestamps,
                    extract_mp3, only_mp3,
                    progress_hook=make_progress_hook(job_id, u),
                )
                if not download_video_file and not only_mp3 and not subtitle_only_placeholder:
                    for p in target_dir.glob("*.mp4"):
                        if p.stat().st_size == 0:
                            p.unlink(missing_ok=True)
                emit(job_id, "item_done", {"url": u, "index": i, "total": total, "ok": True})
            except Exception as e:
                errors.append({"url": u, "error": str(e)})
                emit(job_id, "item_done", {"url": u, "index": i, "total": total, "ok": False, "error": str(e)})

        if topic:
            meta = load_meta()
            for u in final_urls:
                q = urlparse(u).query or ""
                vid = None
                if "v=" in q:
                    for pair in q.split("&"):
                        if pair.startswith("v="):
                            vid = pair.split("=", 1)[1]
                            break
                if vid:
                    meta.setdefault("video_topics", {})[vid] = topic
            save_meta(meta)

        if temp_cookie_path and os.path.exists(temp_cookie_path):
            os.unlink(temp_cookie_path)

        allf = list_files()
        newf = [x for x in allf if x["path"] not in before]
        emit(job_id, "complete", {
            "ok": len(errors) == 0, "processed": total,
            "expanded_from": len(urls), "errors": errors,
            "new_files": newf,
        })
    except Exception as e:
        emit(job_id, "complete", {"ok": False, "errors": [{"error": str(e)}]})


def _run_fetch_missing(payload: dict, job_id: str | None) -> None:
    try:
        video_id = (payload.get("video_id") or "").strip()
        action = (payload.get("action") or "").strip()
        topic = (payload.get("topic") or "").strip()
        langs = payload.get("langs") or ["en", "de", "es"]

        if not video_id or len(video_id) != 11:
            emit(job_id, "complete", {"ok": False, "errors": ["video_id inválido"]})
            return
        if action not in {"subtitles", "video", "thumbnail", "mp3"}:
            emit(job_id, "complete", {"ok": False, "errors": ["action inválida"]})
            return

        url = f"https://www.youtube.com/watch?v={video_id}"
        existing = list_files()
        before = {f["path"] for f in existing}
        errors = []

        # Resolve target dir: prefer explicit topic, else existing files' topic, else flat
        explicit_topic = topic
        if not explicit_topic:
            for f in existing:
                if f.get("video_id") == video_id and f.get("topic"):
                    explicit_topic = f["topic"]
                    break
        target_dir = _target_dir_for_topic(explicit_topic)

        emit(job_id, "start", {"total": 1, "video_id": video_id, "action": action})
        emit(job_id, "item_start", {"url": url, "index": 1, "total": 1, "action": action})

        try:
            if action == "thumbnail":
                thumb_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                out = target_dir / f"thumbnail_[{video_id}].jpg"
                with urlopen(thumb_url, timeout=20) as r:
                    out.write_bytes(r.read())
            elif action == "mp3":
                downloader.download_video(
                    url, str(target_dir), False, None, None, langs, False, False, False, True,
                    progress_hook=make_progress_hook(job_id, url),
                )
            elif action == "video":
                downloader.download_video(
                    url, str(target_dir), True, None, None, langs, False, False, False, False,
                    progress_hook=make_progress_hook(job_id, url),
                )
            else:
                downloader.download_video(
                    url, str(target_dir), False, None, None, langs, False, False, False, False,
                    progress_hook=make_progress_hook(job_id, url),
                )
            emit(job_id, "item_done", {"url": url, "index": 1, "total": 1, "ok": True, "action": action})
        except Exception as e:
            errors.append(str(e))
            emit(job_id, "item_done", {"url": url, "index": 1, "total": 1, "ok": False, "error": str(e), "action": action})

        if topic:
            meta = load_meta()
            meta.setdefault("video_topics", {})[video_id] = topic
            save_meta(meta)

        allf = list_files()
        newf = [x for x in allf if x["path"] not in before]
        emit(job_id, "complete", {"ok": len(errors) == 0, "errors": errors, "new_files": newf})
    except Exception as e:
        emit(job_id, "complete", {"ok": False, "errors": [str(e)]})


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args, **_kw):
        return  # quiet

    def _send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str):
        if not path.exists() or not path.is_file():
            self.send_error(404, "Not found")
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_sse(self, job_id: str):
        q = _job_q(job_id)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(f"event: open\ndata: {json.dumps({'job_id': job_id})}\n\n".encode("utf-8"))
            self.wfile.flush()
            while True:
                try:
                    event, data = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")
                self.wfile.write(payload)
                self.wfile.flush()
                if event == "complete":
                    break
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            _job_close(job_id)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return self._send_file(INDEX_FILE, "text/html; charset=utf-8")
        if parsed.path == "/favicon.svg":
            return self._send_file(FAVICON_FILE, "image/svg+xml")
        if parsed.path == "/api/version":
            return self._send_json({"version": app_version()})
        if parsed.path == "/api/files":
            return self._send_json({"files": list_files()})
        if parsed.path == "/api/topics":
            meta = load_meta()
            topics = sorted({t for t in meta.get("video_topics", {}).values() if t})
            return self._send_json({"topics": topics})
        if parsed.path == "/api/events":
            qs = parse_qs(parsed.query)
            job_id = (qs.get("job_id") or [""])[0]
            if not job_id:
                return self._send_json({"error": "job_id requerido"}, 400)
            return self._handle_sse(job_id)
        if parsed.path.startswith("/media/"):
            rel = unquote(parsed.path[len("/media/"):])
            rel_path = Path(rel)
            if ".." in rel_path.parts:
                return self._send_json({"error": "Invalid path"}, 400)
            full = (DOWNLOADS_DIR / rel_path).resolve()
            if not str(full).startswith(str(DOWNLOADS_DIR.resolve())):
                return self._send_json({"error": "Invalid path"}, 400)
            ext = full.suffix.lower()
            ctype = "application/octet-stream"
            if ext == ".mp4":
                ctype = "video/mp4"
            elif ext == ".srt":
                ctype = "text/plain; charset=utf-8"
            elif ext == ".mp3":
                ctype = "audio/mpeg"
            elif ext == ".zip":
                ctype = "application/zip"
            return self._send_file(full, ctype)
        self.send_error(404, "Not found")

    def _read_json(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self._send_json({"error": "JSON inválido"}, 400)
            return None

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/download":
            payload = self._read_json()
            if payload is None:
                return
            job_id = (payload.get("job_id") or "").strip() or uuid.uuid4().hex
            _job_q(job_id)  # ensure queue exists before worker starts
            threading.Thread(target=_run_download, args=(payload, job_id), daemon=True).start()
            return self._send_json({"ok": True, "job_id": job_id}, 202)
        if parsed.path == "/api/fetch-missing":
            payload = self._read_json()
            if payload is None:
                return
            job_id = (payload.get("job_id") or "").strip() or uuid.uuid4().hex
            _job_q(job_id)
            threading.Thread(target=_run_fetch_missing, args=(payload, job_id), daemon=True).start()
            return self._send_json({"ok": True, "job_id": job_id}, 202)
        if parsed.path == "/api/collect-content":
            payload = self._read_json()
            if payload is None:
                return
            job_id = (payload.get("job_id") or "").strip() or uuid.uuid4().hex
            _job_q(job_id)
            threading.Thread(target=_run_collect_content, args=(payload, job_id), daemon=True).start()
            return self._send_json({"ok": True, "job_id": job_id}, 202)
        if parsed.path == "/api/assign-topic":
            payload = self._read_json()
            if payload is None:
                return
            ids = payload.get("video_ids") or []
            ids = [str(x).strip() for x in ids if str(x).strip()]
            topic = (payload.get("topic") or "").strip()
            result = _assign_topic_to_videos(ids, topic)
            return self._send_json(result, 200 if result.get("ok") else 400)
        if parsed.path == "/api/delete":
            payload = self._read_json()
            if payload is None:
                return
            ids = payload.get("video_ids") or []
            single = (payload.get("video_id") or "").strip()
            if single:
                ids = list(ids) + [single]
            ids = [str(x).strip() for x in ids if str(x).strip()]
            removed_all: list[str] = []
            invalid: list[str] = []
            for vid in ids:
                if len(vid) != 11:
                    invalid.append(vid)
                    continue
                removed_all.extend(_delete_video_files(vid))
            return self._send_json({
                "ok": not invalid,
                "removed": removed_all,
                "count": len(removed_all),
                "invalid_ids": invalid,
            })
        self.send_error(404, "Not found")


def run(host="127.0.0.1", port=8000):
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Frontend: http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run web frontend for downloader")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    run(args.host, args.port)
