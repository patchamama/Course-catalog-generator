#!/usr/bin/env python3
"""
Course Catalog Generator
Scans a directory of courses and generates both a Markdown and HTML catalog
with interactive features including video playback, file previews, search,
notes, and dark/light mode.

Usage:
    python catalog_generator.py <courses_dir> [--depth 2] [--output ./output]
"""

import os
import sys
import json
import argparse
import re
from pathlib import Path
from datetime import datetime
from html import escape as html_escape

# ─── File type mappings ───────────────────────────────────────────────────────

FILE_ICONS = {
    # Video
    "mp4": "🎬", "mkv": "🎬", "avi": "🎬", "mov": "🎬", "webm": "🎬", "flv": "🎬",
    "wmv": "🎬", "m4v": "🎬",
    # Audio
    "mp3": "🎵", "wav": "🎵", "flac": "🎵", "aac": "🎵", "ogg": "🎵", "m4a": "🎵",
    # Images
    "jpg": "🖼️", "jpeg": "🖼️", "png": "🖼️", "gif": "🖼️", "webp": "🖼️",
    "svg": "🖼️", "bmp": "🖼️", "tiff": "🖼️", "ico": "🖼️",
    # Documents
    "pdf": "📄", "docx": "📝", "doc": "📝", "odt": "📝",
    "pptx": "📊", "ppt": "📊", "odp": "📊",
    "xlsx": "📈", "xls": "📈", "ods": "📈", "csv": "📈",
    # Code / Text
    "md": "📋", "txt": "📋", "html": "🌐", "htm": "🌐",
    "py": "🐍", "js": "⚡", "ts": "⚡", "css": "🎨", "json": "🔧",
    "xml": "🔧", "yaml": "🔧", "yml": "🔧", "toml": "🔧", "ini": "🔧",
    "sh": "⚙️", "bash": "⚙️", "zsh": "⚙️", "bat": "⚙️", "ps1": "⚙️",
    "java": "☕", "cpp": "⚙️", "c": "⚙️", "h": "⚙️", "cs": "⚙️",
    "go": "🐹", "rs": "🦀", "rb": "💎", "php": "🐘", "swift": "🍎",
    "kt": "📱", "r": "📊", "sql": "🗃️",
    # Archives
    "zip": "📦", "tar": "📦", "gz": "📦", "rar": "📦", "7z": "📦",
    "bz2": "📦", "xz": "📦",
    # Misc
    "exe": "⚙️", "dmg": "💿", "iso": "💿", "apk": "📱",
    "ipynb": "📓",
    "srt": "💬", "vtt": "💬",
    # ELO DMS
    "eloinst": "📦",
}

VIDEO_EXTS = {"mp4", "mkv", "avi", "mov", "webm", "m4v", "flv", "wmv"}
AUDIO_EXTS = {"mp3", "wav", "flac", "aac", "ogg", "m4a"}
SUBTITLE_EXTS = {"srt", "vtt"}
IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "ico"}
PDF_EXTS = {"pdf"}
TEXT_EXTS = {"txt", "md", "py", "js", "ts", "css", "json", "xml", "yaml",
             "yml", "toml", "ini", "sh", "bash", "html", "htm", "csv", "sql",
             "java", "cpp", "c", "h", "cs", "go", "rs", "rb", "php", "r"}
OFFICE_EXTS = {"docx", "doc", "pptx", "ppt", "xlsx", "xls", "odp", "odt", "ods"}
ARCHIVE_EXTS = {"zip", "tar", "gz", "rar", "7z", "bz2", "xz"}
ELODMS_EXTS = {"eloinst"}

CATEGORY_LABELS = {
    "video": "Videos",
    "audio": "Audio",
    "subtitle": "Subtitles",
    "image": "Images",
    "pdf": "PDFs",
    "text": "Documents & Code",
    "office": "Office Files",
    "elodms": "ELO Packages",
    "archive": "Archives",
    "other": "Other Files",
}

CATEGORY_ICONS_MD = {
    "video": "🎬",
    "audio": "🎵",
    "subtitle": "💬",
    "image": "🖼️",
    "pdf": "📄",
    "text": "📋",
    "office": "📁",
    "elodms": "📦",
    "archive": "📦",
    "other": "📎",
}

CAT_ORDER = ["video", "audio", "subtitle", "pdf", "image", "office", "text", "elodms", "archive", "other"]

# YouTube ID regex: matches an 11-char YouTube video ID at the end of a filename stem
YOUTUBE_ID_RE = re.compile(r'\[([a-zA-Z0-9_-]{11})\]\s*$')


def extract_youtube_id(stem):
    """Extract YouTube video ID from a filename stem like 'Lesson 01 [dQw4w9WgXcQ]'."""
    m = YOUTUBE_ID_RE.search(stem)
    return m.group(1) if m else None


def get_file_icon(ext):
    return FILE_ICONS.get(ext.lower(), "📎")


def get_file_category(ext):
    e = ext.lower()
    if e in VIDEO_EXTS:    return "video"
    if e in AUDIO_EXTS:    return "audio"
    if e in SUBTITLE_EXTS: return "subtitle"
    if e in IMAGE_EXTS:    return "image"
    if e in PDF_EXTS:      return "pdf"
    if e in TEXT_EXTS:     return "text"
    if e in OFFICE_EXTS:   return "office"
    if e in ELODMS_EXTS:   return "elodms"
    if e in ARCHIVE_EXTS:  return "archive"
    return "other"


def human_size(size_bytes):
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def scan_directory(base_path, max_depth=2):
    """Scan courses directory and return structured data."""
    courses = []
    base = Path(base_path).resolve()

    if not base.exists():
        print(f"Error: Directory '{base_path}' does not exist.", file=sys.stderr)
        sys.exit(1)

    # Each top-level directory is a course
    course_dirs = sorted([d for d in base.iterdir() if d.is_dir()])

    for course_dir in course_dirs:
        course = {
            "name": course_dir.name,
            "path": str(course_dir),
            "rel_path": str(course_dir.relative_to(base)),
            "files": [],
            "total_size": 0,
            "file_count": 0,
        }

        # Walk up to max_depth levels inside the course directory
        _collect_files(course_dir, course_dir, 1, max_depth, course)
        courses.append(course)

    return courses


def _collect_files(root, current_dir, current_depth, max_depth, course):
    """Recursively collect files up to max_depth."""
    try:
        entries = sorted(current_dir.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
    except PermissionError:
        return

    for entry in entries:
        if entry.name.startswith("."):
            continue

        if entry.is_dir() and current_depth < max_depth:
            _collect_files(root, entry, current_depth + 1, max_depth, course)
        elif entry.is_file():
            ext = entry.suffix.lstrip(".")
            try:
                size = entry.stat().st_size
            except Exception:
                size = 0

            rel = entry.relative_to(root)
            youtube_id = extract_youtube_id(entry.stem) if ext.lower() in VIDEO_EXTS else None
            course["files"].append({
                "name": entry.name,
                "rel_path": str(rel).replace("\\", "/"),
                "abs_path": str(entry),
                "ext": ext,
                "icon": get_file_icon(ext),
                "category": get_file_category(ext),
                "size": size,
                "size_human": human_size(size),
                "subdir": str(rel.parent) if str(rel.parent) != "." else "",
                "youtube_id": youtube_id,
            })
            course["total_size"] += size
            course["file_count"] += 1


def categorize_files(files):
    """Group files by category."""
    cats = {}
    for f in files:
        cat = f["category"]
        if cat not in cats:
            cats[cat] = []
        cats[cat].append(f)
    return cats


def group_files_by_subdir(files):
    """Separate root-level files from files in subdirectories.

    Returns (root_files, subdir_map) where subdir_map is an ordered dict
    mapping subdir name -> list of files.
    """
    root_files = []
    subdir_map = {}
    for f in files:
        sd = f["subdir"]
        if not sd:
            root_files.append(f)
        else:
            if sd not in subdir_map:
                subdir_map[sd] = []
            subdir_map[sd].append(f)
    return root_files, subdir_map


# ─── Markdown Generator ───────────────────────────────────────────────────────

def generate_markdown(courses, output_dir, courses_base_path):
    # Filter out empty courses
    courses = [c for c in courses if c["file_count"] > 0]

    lines = []
    lines.append("# 📚 Course Catalog\n")
    lines.append(f"> Generated on {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
                 f"{len(courses)} courses found\n")
    lines.append("---\n")

    # Table of contents
    lines.append("## 📑 Table of Contents\n")
    for i, course in enumerate(courses, 1):
        anchor = re.sub(r"[^a-z0-9\-]", "", course["name"].lower().replace(" ", "-"))
        lines.append(f"{i}. [{course['name']}](#{anchor})\n")
    lines.append("\n---\n")

    for course in courses:
        name = course["name"]
        anchor = re.sub(r"[^a-z0-9\-]", "", name.lower().replace(" ", "-"))
        cats = categorize_files(course["files"])

        lines.append(f"## 🎓 {name}\n")
        lines.append(f"*{course['file_count']} files · {human_size(course['total_size'])}*\n\n")

        if not course["files"]:
            lines.append("*No files found in this course.*\n\n")
            lines.append("---\n")
            continue

        # Stats per category
        stat_parts = []
        for cat, label in CATEGORY_LABELS.items():
            if cat in cats:
                icon = CATEGORY_ICONS_MD[cat]
                stat_parts.append(f"{icon} {len(cats[cat])} {label}")
        if stat_parts:
            lines.append(" · ".join(stat_parts) + "\n\n")

        for cat in CAT_ORDER:
            if cat not in cats:
                continue
            icon = CATEGORY_ICONS_MD[cat]
            label = CATEGORY_LABELS[cat]
            lines.append(f"### {icon} {label}\n\n")

            for f in cats[cat]:
                file_icon = f["icon"]
                subdir = f"*{f['subdir']}/* " if f["subdir"] else ""
                # Strip "file:" prefix so links work as //server/path (UNC-style)
                abs_path_uri = Path(f["abs_path"]).as_uri().replace("file:", "", 1)
                # Inline thumbnail for image files (small, next to link)
                thumbnail = ""
                if f["category"] == "image":
                    thumbnail = f' <img src="{abs_path_uri}" height="18" style="vertical-align:middle;max-width:60px">'
                # Use <uri> bracket syntax to handle spaces and special chars
                lines.append(f"- {file_icon} [{f['name']}](<{abs_path_uri}>){thumbnail} {subdir}· `{f['size_human']}`\n")
            lines.append("\n")

        lines.append("---\n\n")

    md_path = Path(output_dir) / "course_catalog.md"
    md_path.write_text("".join(lines), encoding="utf-8")
    return str(md_path)


# ─── HTML Generator ───────────────────────────────────────────────────────────

def file_to_html_entry(f, course_idx, force_youtube=False):
    """Generate an HTML element for a single file."""
    name = f["name"]
    name_safe = html_escape(name)
    ext = f["ext"].lower()
    icon = f["icon"]
    size = f["size_human"]
    cat = f["category"]
    youtube_id = f.get("youtube_id")

    # Build URI from abs_path
    abs_path_uri = Path(f["abs_path"]).as_uri()
    # Escape quotes in URI for use in HTML attributes
    abs_path_attr = abs_path_uri.replace('"', '&quot;')
    abs_path_data = html_escape(f["abs_path"])

    subdir_badge = f'<span class="subdir-badge">{html_escape(f["subdir"])}</span>' if f["subdir"] else ""

    # Build preview/action based on file type
    if cat == "video":
        if youtube_id and force_youtube:
            ytid_safe = html_escape(youtube_id)
            content = f'''
        <div class="file-entry video-entry" data-name="{html_escape(name.lower())}" data-cat="{cat}">
          <div class="file-header" onclick="togglePreview(this)">
            <span class="file-icon">{icon}</span>
            <span class="file-name">{name_safe}</span>
            {subdir_badge}
            <span class="file-size">{html_escape(size)}</span>
            <span class="expand-btn">▶ Play</span>
          </div>
          <div class="file-preview collapsed" data-ytid="{ytid_safe}">
            <div class="youtube-container">
              <iframe allowfullscreen allow="autoplay; encrypted-media" title="{name_safe}"></iframe>
            </div>
          </div>
        </div>'''
        else:
            video_type = ext if ext != "mkv" else "mp4"
            content = f'''
        <div class="file-entry video-entry" data-name="{html_escape(name.lower())}" data-cat="{cat}" data-src="{abs_path_attr}" data-abs-path="{abs_path_data}">
          <div class="file-header" onclick="togglePreview(this)">
            <span class="file-icon">{icon}</span>
            <span class="file-name">{name_safe}</span>
            {subdir_badge}
            <span class="file-size">{html_escape(size)}</span>
            <span class="duration-hint" data-duration="">⏱</span>
            <span class="expand-btn">▶ Play</span>
            <span class="expand-btn vlc-launch" onclick="event.stopPropagation();launchVLCFromCatalog(this.closest('.video-entry').dataset.absPath)" title="Open in VLC">🖥 VLC</span>
            <span class="expand-btn pip-launch" onclick="event.stopPropagation();launchPiP(this.closest('.video-entry').dataset.absPath,this.closest('.video-entry').dataset.name)" title="Float video">⧉ Float</span>
            <button class="permalink-btn" onclick="event.stopPropagation();copyPermalink(this.closest('.file-entry').dataset.absPath)" title="Copy permalink">🔗</button>
          </div>
          <div class="file-preview collapsed">
            <video controls preload="none" class="video-player">
              <source src="{abs_path_attr}" type="video/{html_escape(video_type)}">
              Your browser does not support this video format.
            </video>
          </div>
        </div>'''

    elif cat == "audio":
        content = f'''
        <div class="file-entry audio-entry" data-name="{html_escape(name.lower())}" data-cat="{cat}" data-src="{abs_path_attr}" data-abs-path="{abs_path_data}">
          <div class="file-header" onclick="togglePreview(this)">
            <span class="file-icon">{icon}</span>
            <span class="file-name">{name_safe}</span>
            {subdir_badge}
            <span class="file-size">{html_escape(size)}</span>
            <span class="duration-hint" data-duration="">⏱</span>
            <span class="expand-btn">▶ Play</span>
            <button class="permalink-btn" onclick="event.stopPropagation();copyPermalink(this.closest('.file-entry').dataset.absPath)" title="Copy permalink">🔗</button>
          </div>
          <div class="file-preview collapsed">
            <audio controls class="audio-player">
              <source src="{abs_path_attr}">
            </audio>
          </div>
        </div>'''

    elif cat == "image":
        content = f'''
        <div class="file-entry image-entry" data-name="{html_escape(name.lower())}" data-cat="{cat}" data-abs-path="{abs_path_data}">
          <div class="file-header" onclick="openImageModal(this)" data-file-path="{abs_path_attr}" data-file-name="{name_safe}">
            <span class="file-icon">{icon}</span>
            <img src="{abs_path_attr}" class="file-thumbnail" loading="lazy" onerror="this.style.display=&apos;none&apos;" alt="">
            <span class="file-name">{name_safe}</span>
            {subdir_badge}
            <span class="file-size">{html_escape(size)}</span>
            <span class="expand-btn">👁 View</span>
            <button class="permalink-btn" onclick="event.stopPropagation();copyPermalink(this.closest('.file-entry').dataset.absPath)" title="Copy permalink">🔗</button>
          </div>
        </div>'''

    elif cat == "pdf":
        content = f'''
        <div class="file-entry pdf-entry" data-name="{html_escape(name.lower())}" data-cat="{cat}" data-abs-path="{abs_path_data}">
          <div class="file-header" onclick="openPDF(this)" data-file-path="{abs_path_attr}" data-file-name="{name_safe}">
            <span class="file-icon">{icon}</span>
            <span class="file-name">{name_safe}</span>
            {subdir_badge}
            <span class="file-size">{html_escape(size)}</span>
            <span class="expand-btn">📄 Open</span>
            <button class="permalink-btn" onclick="event.stopPropagation();copyPermalink(this.closest('.file-entry').dataset.absPath)" title="Copy permalink">🔗</button>
          </div>
        </div>'''

    elif cat == "text":
        if ext in ("html", "htm"):
            # HTML files: open directly in new tab
            content = f'''
        <div class="file-entry text-entry" data-name="{html_escape(name.lower())}" data-cat="{cat}" data-abs-path="{abs_path_data}">
          <div class="file-header" onclick="window.open('{abs_path_attr}', '_blank')" data-file-path="{abs_path_attr}" data-file-name="{name_safe}">
            <span class="file-icon">{icon}</span>
            <span class="file-name">{name_safe}</span>
            {subdir_badge}
            <span class="file-size">{html_escape(size)}</span>
            <span class="expand-btn">🌐 Open</span>
            <button class="permalink-btn" onclick="event.stopPropagation();copyPermalink(this.closest('.file-entry').dataset.absPath)" title="Copy permalink">🔗</button>
          </div>
        </div>'''
        else:
            content = f'''
        <div class="file-entry text-entry" data-name="{html_escape(name.lower())}" data-cat="{cat}" data-abs-path="{abs_path_data}">
          <div class="file-header" onclick="openTextFile(this)" data-file-path="{abs_path_attr}" data-file-name="{name_safe}">
            <span class="file-icon">{icon}</span>
            <span class="file-name">{name_safe}</span>
            {subdir_badge}
            <span class="file-size">{html_escape(size)}</span>
            <span class="expand-btn">📋 View</span>
            <button class="permalink-btn" onclick="event.stopPropagation();copyPermalink(this.closest('.file-entry').dataset.absPath)" title="Copy permalink">🔗</button>
          </div>
        </div>'''

    elif cat == "subtitle":
        content = f'''
        <div class="file-entry subtitle-entry" data-name="{html_escape(name.lower())}" data-cat="{cat}" data-abs-path="{abs_path_data}">
          <div class="file-header" onclick="openTextFile(this)" data-file-path="{abs_path_attr}" data-file-name="{name_safe}">
            <span class="file-icon">{icon}</span>
            <span class="file-name">{name_safe}</span>
            {subdir_badge}
            <span class="file-size">{html_escape(size)}</span>
            <span class="expand-btn">💬 View</span>
            <button class="permalink-btn" onclick="event.stopPropagation();copyPermalink(this.closest('.file-entry').dataset.absPath)" title="Copy permalink">🔗</button>
          </div>
        </div>'''

    elif cat == "elodms":
        # Build elodms:// URI: strip "file://" prefix and replace with "elodms://"
        elodms_href = abs_path_uri.replace("file://", "elodms://", 1)
        elodms_href_attr = elodms_href.replace('"', '&quot;')
        content = f'''
        <div class="file-entry elodms-entry" data-name="{html_escape(name.lower())}" data-cat="{cat}" data-abs-path="{abs_path_data}">
          <div class="file-header">
            <span class="file-icon">{icon}</span>
            <span class="file-name">{name_safe}</span>
            {subdir_badge}
            <span class="file-size">{html_escape(size)}</span>
            <a href="{elodms_href_attr}" class="expand-btn btn-elodms" title="Install via ELO Java Client">⚙ Install</a>
            <button class="permalink-btn" onclick="event.stopPropagation();copyPermalink(this.closest('.file-entry').dataset.absPath)" title="Copy permalink">🔗</button>
          </div>
        </div>'''

    else:
        content = f'''
        <div class="file-entry other-entry" data-name="{html_escape(name.lower())}" data-cat="{cat}" data-abs-path="{abs_path_data}">
          <div class="file-header">
            <span class="file-icon">{icon}</span>
            <a href="{abs_path_attr}" class="file-name" target="_blank">{name_safe}</a>
            {subdir_badge}
            <span class="file-size">{html_escape(size)}</span>
            <span class="expand-btn file-open-link"><a href="{abs_path_attr}" target="_blank">Open ↗</a></span>
            <button class="permalink-btn" onclick="event.stopPropagation();copyPermalink(this.closest('.file-entry').dataset.absPath)" title="Copy permalink">🔗</button>
          </div>
        </div>'''

    return content


def generate_html(courses, output_dir, force_youtube=False):
    """Generate the full HTML catalog."""

    # Filter out empty courses
    courses = [c for c in courses if c["file_count"] > 0]

    courses_js = []
    courses_html_parts = []

    for cidx, course in enumerate(courses):
        cats = categorize_files(course["files"])
        name = course["name"]
        count = course["file_count"]
        total = human_size(course["total_size"])

        # Category summary badges
        badge_html = ""
        for cat in CAT_ORDER:
            if cat in cats:
                badge_html += f'<span class="cat-badge cat-{cat}">{CATEGORY_ICONS_MD[cat]} {len(cats[cat])} {CATEGORY_LABELS[cat]}</span>'

        # Separate root files from subdirectory files
        root_files, subdir_map = group_files_by_subdir(course["files"])
        root_cats = categorize_files(root_files)

        # Files HTML per category (root files only)
        files_html = ""
        for cat in CAT_ORDER:
            if cat not in root_cats:
                continue
            label = CATEGORY_LABELS[cat]
            icon = CATEGORY_ICONS_MD[cat]
            files_html += f'<div class="cat-group" data-cat-group="{cat}"><div class="cat-group-header">{icon} {label}</div><div class="cat-group-body">'
            for f in root_cats[cat]:
                files_html += file_to_html_entry(f, cidx, force_youtube)
            files_html += "</div></div>"

        # Subdirectory groups
        for sd_idx, (sd_name, sd_files) in enumerate(subdir_map.items()):
            sd_id = f"subdir-{cidx}-{sd_idx}"
            sd_cats = categorize_files(sd_files)
            sd_inner = ""
            for cat in CAT_ORDER:
                if cat not in sd_cats:
                    continue
                label = CATEGORY_LABELS[cat]
                icon = CATEGORY_ICONS_MD[cat]
                sd_inner += f'<div class="cat-group" data-cat-group="{cat}"><div class="cat-group-header">{icon} {label}</div><div class="cat-group-body">'
                for f in sd_cats[cat]:
                    sd_inner += file_to_html_entry(f, cidx, force_youtube)
                sd_inner += "</div></div>"

            # Subdir category badges
            sd_cat_counts = {}
            for f in sd_files:
                sd_cat_counts[f["category"]] = sd_cat_counts.get(f["category"], 0) + 1
            sd_badges = "".join(
                f'<span class="cat-badge cat-{c}" style="font-size:0.68rem;padding:2px 7px">'
                f'{CATEGORY_ICONS_MD[c]} {sd_cat_counts[c]}</span>'
                for c in CAT_ORDER if c in sd_cat_counts
            )

            sd_name_safe = html_escape(sd_name)
            sd_abs_path = html_escape(str(Path(sd_files[0]["abs_path"]).parent)) if sd_files else ""
            files_html += f'''<div class="subdir-group" id="{sd_id}" data-abs-path="{sd_abs_path}">
          <div class="subdir-header" onclick="toggleSubdir('{sd_id}')">
            <span class="subdir-chevron">▶</span>
            <span class="subdir-folder-icon">📁</span>
            <span class="subdir-name">{sd_name_safe}</span>
            <div class="subdir-badges-row">{sd_badges}</div>
            <span class="subdir-count">{len(sd_files)} files</span>
            <button class="permalink-btn" onclick="event.stopPropagation();copyPermalink(this.closest('.subdir-group').dataset.absPath)" title="Copy folder permalink">🔗</button>
          </div>
          <div class="subdir-body collapsed" id="sdbody-{sd_id}">
            {sd_inner}
          </div>
        </div>'''

        # Search keywords for JS
        keywords = " ".join(html_escape(f["name"].lower()) for f in course["files"])

        name_safe = html_escape(name)
        course_abs_path = html_escape(course["path"])
        courses_html_parts.append(f'''
    <div class="course-card" id="course-{cidx}" data-name="{html_escape(name.lower())}" data-keywords="{keywords}" data-abs-path="{course_abs_path}">
      <div class="course-header" onclick="toggleCourse({cidx})">
        <div class="course-title-row">
          <span class="course-chevron" id="chevron-{cidx}">▶</span>
          <span class="course-icon">🎓</span>
          <span class="course-title">{name_safe}</span>
          <button class="permalink-btn" onclick="event.stopPropagation();copyPermalink(this.closest('.course-card').dataset.absPath)" title="Copy course permalink">🔗</button>
        </div>
        <div class="course-meta">
          <span class="course-stat">{count} files</span>
          <span class="course-stat">{html_escape(total)}</span>
        </div>
      </div>
      <div class="course-body collapsed" id="body-{cidx}">
        <div class="course-badges">{badge_html}</div>
        <div class="course-files">{files_html}</div>
        <div class="course-notes-area">
          <div class="notes-header">📝 Course Notes</div>
          <textarea class="notes-textarea" id="notes-{cidx}"
            placeholder="Add your notes for this course here..."
            onchange="saveNote({cidx})"></textarea>
          <button class="notes-save-btn" onclick="saveNote({cidx})">💾 Save Note</button>
        </div>
      </div>
    </div>''')

    courses_html = "\n".join(courses_html_parts)

    # Stats for header
    total_files = sum(c["file_count"] for c in courses)
    total_size = human_size(sum(c["total_size"] for c in courses))
    gen_date = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>📚 Course Catalog</title>
  <style>
    /* ─── CSS Variables ─── */
    :root {{
      --bg: #0f1117;
      --bg2: #181c27;
      --bg3: #1e2433;
      --surface: #242938;
      --surface2: #2d3348;
      --border: #353d56;
      --accent: #6c8ef5;
      --accent2: #a78bfa;
      --accent3: #34d399;
      --text: #e8ecf4;
      --text2: #9aa3b8;
      --text3: #636b85;
      --danger: #f87171;
      --warning: #fbbf24;
      --shadow: 0 4px 24px rgba(0,0,0,0.4);
      --radius: 12px;
      --radius-sm: 8px;
      --font: 'Segoe UI', system-ui, -apple-system, sans-serif;
      --font-mono: 'Cascadia Code', 'Fira Code', 'Courier New', monospace;
      --transition: 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    }}
    body.light {{
      --bg: #f0f4ff;
      --bg2: #e8edf8;
      --bg3: #dde4f5;
      --surface: #ffffff;
      --surface2: #f5f7ff;
      --border: #c8d0e8;
      --accent: #4469e8;
      --accent2: #7c5cf0;
      --accent3: #059669;
      --text: #1a2040;
      --text2: #4a5568;
      --text3: #8898aa;
      --danger: #dc2626;
      --warning: #d97706;
      --shadow: 0 4px 24px rgba(100,120,200,0.12);
    }}

    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: var(--font);
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      transition: background var(--transition), color var(--transition);
    }}

    /* ─── Header ─── */
    .header {{
      background: linear-gradient(135deg, var(--bg2) 0%, var(--bg3) 100%);
      border-bottom: 1px solid var(--border);
      padding: 24px 32px;
      position: sticky;
      top: 0;
      z-index: 100;
      backdrop-filter: blur(12px);
    }}
    .header-top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      flex-wrap: wrap;
    }}
    .header-title {{
      font-size: 1.8rem;
      font-weight: 800;
      background: linear-gradient(135deg, var(--accent), var(--accent2));
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
      letter-spacing: -0.5px;
    }}
    .header-subtitle {{
      font-size: 0.85rem;
      color: var(--text3);
      margin-top: 4px;
    }}
    .header-actions {{
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }}
    .btn {{
      padding: 8px 16px;
      border-radius: var(--radius-sm);
      border: 1px solid var(--border);
      background: var(--surface);
      color: var(--text);
      cursor: pointer;
      font-size: 0.85rem;
      font-family: var(--font);
      transition: all var(--transition);
      white-space: nowrap;
    }}
    .btn:hover {{ background: var(--surface2); border-color: var(--accent); }}
    .btn-accent {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
    .btn-accent:hover {{ opacity: 0.85; }}

    /* ─── Search ─── */
    .search-bar {{
      margin-top: 16px;
      position: relative;
    }}
    .search-bar input {{
      width: 100%;
      padding: 12px 48px 12px 16px;
      border-radius: var(--radius);
      border: 1px solid var(--border);
      background: var(--surface);
      color: var(--text);
      font-size: 1rem;
      font-family: var(--font);
      transition: border-color var(--transition);
      outline: none;
    }}
    .search-bar input:focus {{ border-color: var(--accent); }}
    .search-bar .search-icon {{
      position: absolute;
      right: 16px;
      top: 50%;
      transform: translateY(-50%);
      color: var(--text3);
      pointer-events: none;
    }}
    .search-count {{
      font-size: 0.8rem;
      color: var(--text3);
      margin-top: 6px;
      display: none;
    }}

    /* ─── Stats Bar ─── */
    .stats-bar {{
      display: flex;
      gap: 24px;
      padding: 16px 32px;
      background: var(--bg2);
      border-bottom: 1px solid var(--border);
      flex-wrap: wrap;
    }}
    .stat-item {{
      font-size: 0.85rem;
      color: var(--text2);
    }}
    .stat-item strong {{ color: var(--accent); }}

    /* ─── Main Content ─── */
    .main {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 24px 24px 80px;
    }}

    /* ─── Course Card ─── */
    .course-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      margin-bottom: 12px;
      overflow: hidden;
      transition: border-color var(--transition), box-shadow var(--transition);
    }}
    .course-card:hover {{ border-color: var(--accent); box-shadow: var(--shadow); }}
    .course-card.search-hidden {{ display: none; }}
    .course-card.filter-type-hidden {{ display: none; }}
    .course-card.search-match {{ border-color: var(--accent3); }}

    .course-header {{
      padding: 18px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      cursor: pointer;
      user-select: none;
      gap: 12px;
    }}
    .course-header:hover {{ background: var(--surface2); }}
    .course-title-row {{
      display: flex;
      align-items: center;
      gap: 12px;
      flex: 1;
      min-width: 0;
    }}
    .course-chevron {{
      font-size: 0.75rem;
      color: var(--text3);
      transition: transform var(--transition);
      flex-shrink: 0;
    }}
    .course-chevron.open {{ transform: rotate(90deg); color: var(--accent); }}
    .course-icon {{ font-size: 1.3rem; flex-shrink: 0; }}
    .course-title {{
      font-size: 1.1rem;
      font-weight: 700;
      color: var(--text);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .course-meta {{
      display: flex;
      gap: 12px;
      flex-shrink: 0;
    }}
    .course-stat {{
      font-size: 0.8rem;
      color: var(--text3);
      background: var(--bg3);
      padding: 3px 10px;
      border-radius: 20px;
    }}

    /* ─── Course Body ─── */
    .course-body {{
      border-top: 1px solid var(--border);
      overflow: hidden;
      transition: max-height 0.4s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.3s;
      max-height: 0;
      opacity: 0;
    }}
    .course-body:not(.collapsed) {{
      max-height: none;
      opacity: 1;
    }}
    .course-badges {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      padding: 16px 24px 8px;
    }}
    .cat-badge {{
      font-size: 0.78rem;
      padding: 4px 12px;
      border-radius: 20px;
      font-weight: 500;
    }}
    .cat-badge.cat-video    {{ background: rgba(99,102,241,0.2); color: #818cf8; }}
    .cat-badge.cat-audio    {{ background: rgba(236,72,153,0.2); color: #f472b6; }}
    .cat-badge.cat-subtitle {{ background: rgba(34,197,94,0.12); color: #22c55e; }}
    .cat-badge.cat-pdf      {{ background: rgba(239,68,68,0.2); color: #f87171; }}
    .cat-badge.cat-image    {{ background: rgba(16,185,129,0.2); color: #34d399; }}
    .cat-badge.cat-office  {{ background: rgba(245,158,11,0.2); color: #fbbf24; }}
    .cat-badge.cat-text    {{ background: rgba(59,130,246,0.2); color: #60a5fa; }}
    .cat-badge.cat-archive {{ background: rgba(107,114,128,0.2); color: #9ca3af; }}
    .cat-badge.cat-other   {{ background: rgba(156,163,175,0.15); color: #d1d5db; }}

    /* ─── Category Groups ─── */
    .cat-group {{ margin: 0 16px 4px; }}
    .cat-group.group-hidden {{ display: none; }}
    .cat-group-header {{
      font-size: 0.75rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 1px;
      color: var(--text3);
      padding: 12px 8px 6px;
      border-bottom: 1px solid var(--border);
      margin-bottom: 4px;
    }}
    .cat-group-body {{ padding: 4px 0 8px; }}

    /* ─── Subdir Groups ─── */
    .subdir-group {{
      margin: 8px 16px 4px;
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      overflow: hidden;
    }}
    .subdir-group.group-hidden {{ display: none; }}
    .subdir-header {{
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      cursor: pointer;
      background: var(--bg3);
      user-select: none;
      font-size: 0.85rem;
      font-weight: 600;
    }}
    .subdir-header:hover {{ background: var(--surface2); }}
    .subdir-chevron {{
      font-size: 0.7rem;
      color: var(--text3);
      transition: transform var(--transition);
      flex-shrink: 0;
    }}
    .subdir-chevron.open {{ transform: rotate(90deg); color: var(--accent); }}
    .subdir-folder-icon {{ font-size: 0.95rem; flex-shrink: 0; }}
    .subdir-name {{ flex: 1; color: var(--text2); }}
    .subdir-count {{
      font-size: 0.75rem;
      color: var(--text3);
      background: var(--bg);
      padding: 2px 8px;
      border-radius: 10px;
    }}
    .subdir-body {{
      overflow: hidden;
      max-height: 0;
      opacity: 0;
      transition: max-height 0.35s cubic-bezier(0.4,0,0.2,1), opacity 0.25s;
      padding: 0;
    }}
    .subdir-body:not(.collapsed) {{
      max-height: none;
      opacity: 1;
      padding: 4px 0;
    }}

    /* ─── File Entry ─── */
    .file-entry {{
      border-radius: var(--radius-sm);
      margin: 2px 0;
      transition: background var(--transition);
      overflow: hidden;
    }}
    .file-entry:hover {{ background: var(--surface2); }}
    .file-entry.file-search-hidden {{ display: none; }}

    .file-header {{
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 8px 12px;
      cursor: pointer;
    }}
    .file-icon {{ font-size: 1rem; flex-shrink: 0; }}
    .file-thumbnail {{
      height: 1.6em;
      width: auto;
      max-width: 3em;
      border-radius: 3px;
      object-fit: cover;
      flex-shrink: 0;
    }}
    .file-name {{
      flex: 1;
      font-size: 0.9rem;
      color: var(--text);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      text-decoration: none;
    }}
    .file-name:hover {{ color: var(--accent); }}
    .subdir-badge {{
      font-size: 0.72rem;
      color: var(--text3);
      background: var(--bg3);
      padding: 2px 8px;
      border-radius: 10px;
      flex-shrink: 0;
    }}
    .file-size {{
      font-size: 0.78rem;
      color: var(--text3);
      flex-shrink: 0;
      font-family: var(--font-mono);
    }}
    .duration-hint {{
      font-size: 0.75rem;
      color: var(--text3);
      flex-shrink: 0;
    }}
    .expand-btn {{
      font-size: 0.75rem;
      color: var(--accent);
      flex-shrink: 0;
      font-weight: 600;
    }}
    .file-open-link a {{
      color: var(--accent);
      text-decoration: none;
    }}

    /* ─── Previews ─── */
    .file-preview {{
      display: none;
      padding: 12px;
      overflow: hidden;
    }}
    .file-preview:not(.collapsed) {{ display: block; }}
    .video-player {{
      width: 100%;
      max-height: 420px;
      border-radius: var(--radius-sm);
      background: #000;
    }}
    .audio-player {{ width: 100%; }}
    .image-preview {{
      max-width: 100%;
      max-height: 400px;
      border-radius: var(--radius-sm);
      object-fit: contain;
    }}

    /* ─── YouTube container ─── */
    .youtube-container {{
      position: relative;
      padding-bottom: 56.25%;
      height: 0;
    }}
    .youtube-container iframe {{
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      border: none;
      border-radius: var(--radius-sm);
    }}

    /* ─── Notes ─── */
    .course-notes-area {{
      padding: 16px 24px 24px;
      border-top: 1px solid var(--border);
    }}
    .notes-header {{
      font-size: 0.85rem;
      font-weight: 700;
      color: var(--text2);
      margin-bottom: 10px;
    }}
    .notes-textarea {{
      width: 100%;
      min-height: 100px;
      padding: 12px;
      border-radius: var(--radius-sm);
      border: 1px solid var(--border);
      background: var(--bg);
      color: var(--text);
      font-family: var(--font);
      font-size: 0.9rem;
      resize: vertical;
      transition: border-color var(--transition);
      outline: none;
    }}
    .notes-textarea:focus {{ border-color: var(--accent2); }}
    .notes-save-btn {{
      margin-top: 8px;
      padding: 6px 16px;
      background: var(--accent2);
      color: #fff;
      border: none;
      border-radius: var(--radius-sm);
      cursor: pointer;
      font-size: 0.82rem;
      font-family: var(--font);
      transition: opacity var(--transition);
    }}
    .notes-save-btn:hover {{ opacity: 0.85; }}

    /* ─── Modal ─── */
    .modal-overlay {{
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,0.75);
      z-index: 1000;
      align-items: center;
      justify-content: center;
      padding: 20px;
      backdrop-filter: blur(4px);
    }}
    .modal-overlay.open {{ display: flex; }}
    .modal {{
      background: var(--surface);
      border-radius: var(--radius);
      border: 1px solid var(--border);
      width: 100%;
      max-width: 900px;
      max-height: 90vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      box-shadow: 0 20px 60px rgba(0,0,0,0.6);
      transition: max-width 0.2s, max-height 0.2s, width 0.2s, height 0.2s;
    }}
    .modal.maximized {{
      max-width: 98vw;
      max-height: 98vh;
      width: 98vw;
      height: 98vh;
    }}
    .modal-header {{
      padding: 16px 20px;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }}
    .modal-title {{
      font-weight: 700;
      font-size: 1rem;
      color: var(--text);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      flex: 1;
    }}
    .modal-title a {{
      color: inherit;
      text-decoration: none;
    }}
    .modal-title a:hover {{
      color: var(--accent);
      text-decoration: underline;
    }}
    .modal-maximize {{
      background: none;
      border: none;
      color: var(--text3);
      font-size: 1.2rem;
      cursor: pointer;
      flex-shrink: 0;
      line-height: 1;
      padding: 0 4px;
    }}
    .modal-maximize:hover {{ color: var(--accent); }}
    .modal-close {{
      background: none;
      border: none;
      color: var(--text3);
      font-size: 1.4rem;
      cursor: pointer;
      flex-shrink: 0;
      line-height: 1;
      padding: 0 4px;
    }}
    .modal-close:hover {{ color: var(--danger); }}
    .modal-body {{
      flex: 1;
      overflow: auto;
      padding: 16px 20px;
    }}
    .modal-body pre {{
      white-space: pre-wrap;
      font-family: var(--font-mono);
      font-size: 0.85rem;
      color: var(--text);
      line-height: 1.6;
    }}
    .modal-body iframe {{
      width: 100%;
      height: 75vh;
      border: none;
      border-radius: var(--radius-sm);
    }}
    .modal.maximized .modal-body iframe {{
      height: calc(98vh - 70px);
    }}
    .modal-image-preview {{
      max-width: 100%;
      max-height: calc(90vh - 80px);
      display: block;
      margin: 0 auto;
      border-radius: var(--radius-sm);
      object-fit: contain;
    }}

    /* ─── HTML viewer (rendered + source toggle) ─── */
    .html-viewer-wrap {{ display: block; }}
    .html-viewer-toolbar {{
      position: sticky; top: 0; z-index: 5;
      display: flex; gap: 6px; padding: 6px 10px;
      background: var(--surface2, #1e1e2e); border-bottom: 1px solid var(--border, #444);
      align-items: center;
    }}
    .html-view-btn {{
      font-size: 0.75rem; padding: 3px 12px;
      border: 1px solid var(--border, #666); border-radius: 4px;
      cursor: pointer; background: var(--surface, #2a2a3a); color: var(--text2, #ccc);
      transition: background 0.15s, color 0.15s;
    }}
    .html-view-btn:hover {{ background: var(--surface3, #333); }}
    .html-view-btn.active {{ background: var(--accent, #7c6af7); color: #fff; border-color: var(--accent, #7c6af7); }}
    .html-viewer-content {{ display: block; }}
    .html-viewer-content iframe {{ width: 100%; height: calc(90vh - 100px); min-height: 400px; border: none; display: block; }}
    .modal.maximized .html-viewer-content iframe {{ height: calc(98vh - 100px); }}
    .html-viewer-content pre {{ margin: 0; }}

    /* ─── Notes Export Panel ─── */
    .notes-panel {{
      position: fixed;
      bottom: 24px;
      right: 24px;
      z-index: 200;
    }}
    .notes-panel-btn {{
      width: 52px;
      height: 52px;
      border-radius: 50%;
      background: var(--accent2);
      border: none;
      color: #fff;
      font-size: 1.4rem;
      cursor: pointer;
      box-shadow: 0 4px 16px rgba(0,0,0,0.3);
      transition: transform var(--transition);
    }}
    .notes-panel-btn:hover {{ transform: scale(1.1); }}
    .notes-panel-popup {{
      display: none;
      position: absolute;
      bottom: 60px;
      right: 0;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 16px;
      width: 260px;
      box-shadow: var(--shadow);
    }}
    .notes-panel-popup.open {{ display: block; }}
    .notes-panel-popup h3 {{ font-size: 0.9rem; margin-bottom: 12px; color: var(--text); }}
    .notes-count {{ font-size: 0.8rem; color: var(--text3); margin-bottom: 10px; }}

    /* ─── Theme Toggle ─── */
    .theme-toggle {{ font-size: 1.2rem; }}

    /* ─── Expand All button ─── */
    .toolbar {{
      display: flex;
      gap: 10px;
      margin-bottom: 16px;
      flex-wrap: wrap;
      align-items: center;
    }}
    .filter-btns {{
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }}
    .filter-btn {{
      padding: 5px 12px;
      font-size: 0.78rem;
      border-radius: 20px;
      border: 1px solid var(--border);
      background: var(--surface);
      color: var(--text2);
      cursor: pointer;
      transition: all var(--transition);
    }}
    .filter-btn:hover, .filter-btn.active {{
      background: var(--accent);
      color: #fff;
      border-color: var(--accent);
    }}

    /* ─── Toast ─── */
    .toast {{
      position: fixed;
      bottom: 90px;
      right: 24px;
      background: var(--accent3);
      color: #fff;
      padding: 10px 18px;
      border-radius: var(--radius-sm);
      font-size: 0.85rem;
      font-weight: 600;
      z-index: 300;
      opacity: 0;
      transform: translateY(10px);
      transition: all 0.3s;
      pointer-events: none;
    }}
    .toast.show {{ opacity: 1; transform: translateY(0); }}

    /* ─── ELO DMS ─── */
    .btn-elodms {{ color: var(--warning); font-weight: 600; text-decoration: none; font-size: 0.78rem; }}
    .btn-elodms:hover {{ opacity: 0.8; }}
    .cat-badge.cat-elodms {{ background: rgba(251,191,36,0.2); color: #fbbf24; }}

    /* ─── Subdir badges row ─── */
    .subdir-badges-row {{ display: flex; gap: 4px; flex-wrap: wrap; flex: 1; }}

    /* ─── Backend search panel ─── */
    .search-spinner {{
      position: absolute; right: 16px; top: 50%; transform: translateY(-50%);
      color: var(--accent); font-size: 1.1rem; display: none;
      animation: spin 1s linear infinite;
    }}
    @keyframes spin {{ from {{ transform: translateY(-50%) rotate(0deg); }} to {{ transform: translateY(-50%) rotate(360deg); }} }}
    /* ─── Search Options Panel ─── */
    .search-opts-row {{
      display: flex; align-items: center; gap: 6px; margin-top: 6px;
    }}
    .search-opts-btn {{
      background: none; border: 1px solid var(--border); border-radius: 6px;
      color: var(--text3); cursor: pointer; padding: 3px 9px; font-size: 0.8rem;
      transition: color var(--transition), border-color var(--transition), background var(--transition);
      white-space: nowrap; flex-shrink: 0;
    }}
    .search-opts-btn:hover {{ color: var(--accent); border-color: var(--accent); }}
    .search-opts-btn.active {{ color: var(--accent); border-color: var(--accent); background: rgba(108,142,245,0.08); }}
    .search-opts-badge {{
      display: none; font-size: 0.7rem; background: var(--accent); color: #fff;
      border-radius: 10px; padding: 1px 5px; margin-left: 2px;
    }}
    .search-opts-btn.active .search-opts-badge {{ display: inline; }}
    .search-opts-panel {{
      display: none; margin-top: 6px; padding: 8px 14px;
      background: var(--surface); border: 1px solid var(--border);
      border-radius: var(--radius-sm); flex-direction: column; gap: 0;
      font-size: 0.82rem;
    }}
    .search-opts-panel.open {{ display: flex; }}
    .sopt-section {{
      display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
      padding: 6px 0;
    }}
    .sopt-section + .sopt-section {{
      border-top: 1px solid var(--border); margin-top: 4px; padding-top: 10px;
    }}
    .sopt-section-title {{
      font-size: 0.7rem; font-weight: 700; color: var(--text3);
      text-transform: uppercase; letter-spacing: 0.6px; width: 100%; margin-bottom: 2px;
    }}
    .sopt-label {{
      display: flex; align-items: center; gap: 6px;
      color: var(--text2); cursor: default; user-select: none;
    }}
    .sopt-label select {{
      background: var(--bg2); border: 1px solid var(--border); color: var(--text);
      border-radius: 4px; padding: 2px 6px; font-size: 0.82rem; cursor: pointer;
    }}
    .sopt-check {{ cursor: pointer; }}
    .sopt-check input {{ cursor: pointer; accent-color: var(--accent); }}
    .sopt-hint {{ font-size: 0.75rem; color: var(--text3); border-left: 2px solid var(--border); padding-left: 8px; }}
    .streaming-log {{
      display: none; margin-top: 6px; padding: 5px 12px;
      background: var(--surface); border: 1px solid var(--border);
      border-radius: var(--radius-sm); font-size: 0.8rem;
      color: var(--text2); align-items: center; gap: 10px;
    }}
    .streaming-log.active {{ display: flex; }}
    .streaming-log-text {{ flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .streaming-cancel-btn {{
      background: none; border: 1px solid var(--danger); color: var(--danger);
      border-radius: 4px; padding: 1px 8px; font-size: 0.75rem; cursor: pointer; flex-shrink: 0;
    }}
    .streaming-cancel-btn:hover {{ background: var(--danger); color: #fff; }}
    .sopt-filetypes {{ display: flex; flex-wrap: wrap; gap: 5px; }}
    .sopt-ft-label {{
      display: inline-flex; align-items: center; gap: 3px; font-size: 0.75rem;
      background: var(--bg2); border: 1px solid var(--border); border-radius: 4px;
      padding: 2px 7px; cursor: pointer; user-select: none; color: var(--text2);
      transition: border-color 0.15s, background 0.15s, color 0.15s;
    }}
    .sopt-ft-label:hover {{ border-color: var(--accent); color: var(--accent); }}
    .sopt-ft-label input {{ cursor: pointer; accent-color: var(--accent); margin: 0; }}
    .sopt-ft-label:has(input:checked) {{
      border-color: var(--accent); background: rgba(108,142,245,0.12); color: var(--accent);
    }}
    .sopt-ft-all-btn, .sopt-ft-none-btn {{
      font-size: 0.72rem; padding: 2px 7px; border-radius: 4px; cursor: pointer;
      background: none; border: 1px solid var(--border); color: var(--text3);
    }}
    .sopt-ft-all-btn:hover, .sopt-ft-none-btn:hover {{ border-color: var(--accent); color: var(--accent); }}
    .sopt-cache-btn {{
      background: var(--bg2); border: 1px solid var(--border); border-radius: 4px;
      color: var(--text2); cursor: pointer; padding: 2px 9px; font-size: 0.78rem;
      white-space: nowrap; transition: border-color 0.15s, color 0.15s;
    }}
    .sopt-cache-btn:hover {{ border-color: var(--accent); color: var(--accent); }}
    .sopt-cache-btn:disabled {{ opacity: 0.55; cursor: wait; }}

    .backend-results {{
      margin-top: 8px; border: 1px solid var(--border);
      border-radius: var(--radius-sm); background: var(--surface); overflow: hidden; display: none;
    }}
    .backend-results-header {{
      display: flex; align-items: center; gap: 8px; padding: 10px 14px;
      cursor: pointer; user-select: none; font-size: 0.88rem; font-weight: 600; color: var(--text2);
    }}
    .backend-results-header:hover {{ background: var(--surface2); }}
    .backend-chevron {{ font-size: 0.7rem; color: var(--text3); transition: transform var(--transition); }}
    .backend-chevron.open {{ transform: rotate(90deg); }}
    .backend-results-close {{ margin-left: auto; color: var(--text3); cursor: pointer; font-size: 0.9rem; }}
    .backend-results-close:hover {{ color: var(--danger); }}
    .backend-results-body {{ padding: 0 8px 8px; max-height: 400px; overflow-y: auto; }}
    .backend-result-item {{ border: 1px solid var(--border); border-radius: var(--radius-sm); margin: 6px 0; overflow: hidden; }}
    .backend-result-header {{
      display: flex; align-items: center; gap: 8px; padding: 8px 12px;
      cursor: pointer; background: var(--bg2); font-size: 0.85rem;
    }}
    .backend-result-header:hover {{ background: var(--bg3); }}
    .backend-result-file {{ font-weight: 600; color: var(--text); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .backend-result-path {{ font-size: 0.72rem; color: var(--text3); display: block; }}
    .backend-result-count {{ font-size: 0.75rem; color: var(--accent); white-space: nowrap; }}
    .backend-result-matches {{ display: none; padding: 6px 12px; background: var(--bg); }}
    .backend-result-matches.open {{ display: block; }}
    .backend-match-line {{
      padding: 4px 0; border-bottom: 1px solid var(--border);
      font-size: 0.8rem; font-family: var(--font-mono); color: var(--text2); line-height: 1.5;
    }}
    .backend-match-line:last-child {{ border-bottom: none; }}
    .backend-match-line mark {{ background: rgba(252,211,77,0.25); color: var(--warning); border-radius: 2px; padding: 0 2px; }}
    .backend-match-linenum {{ color: var(--text3); margin-right: 8px; }}
    .backend-result-open-btn {{
      font-size: 0.75rem; padding: 3px 10px; background: var(--accent); color: #fff;
      border: none; border-radius: 4px; cursor: pointer; margin-top: 6px;
    }}
    .backend-result-open-btn:hover {{ opacity: 0.85; }}
    .srt-play-btn {{
      font-size: 0.72rem; padding: 1px 7px; background: #1976d2; color: #fff;
      border: none; border-radius: 4px; cursor: pointer; margin-left: 8px;
      vertical-align: middle; white-space: nowrap;
    }}
    .srt-play-btn:hover {{ background: #1565c0; }}
    .backend-unavailable {{ padding: 12px; font-size: 0.82rem; color: var(--text3); text-align: center; }}

    /* ─── Backend type filters ─── */
    .backend-type-filters {{
      display: flex; gap: 6px; flex-wrap: wrap; padding: 8px 8px 4px;
      border-bottom: 1px solid var(--border); margin-bottom: 4px;
    }}
    .backend-type-btn {{
      padding: 3px 10px; font-size: 0.75rem; border-radius: 14px;
      border: 1px solid var(--border); background: var(--surface2);
      color: var(--text2); cursor: pointer; transition: all var(--transition);
    }}
    .backend-type-btn:hover, .backend-type-btn.active {{
      background: var(--accent); color: #fff; border-color: var(--accent);
    }}
    .backend-match-page {{
      display: inline-block; font-size: 0.7rem; background: rgba(108,142,245,0.15);
      color: var(--accent); border-radius: 3px; padding: 0 4px;
      margin-right: 4px; vertical-align: middle;
    }}

    /* ─── VLC launch button ─── */
    .vlc-launch {{
      color: var(--warning) !important; font-size: 0.72rem;
      border: 1px solid var(--warning); border-radius: 4px;
      padding: 1px 6px; cursor: pointer; white-space: nowrap;
    }}
    .vlc-launch:hover {{ opacity: 0.75; }}

    /* ─── Permalink / deep-link button ─── */
    .permalink-btn {{
      font-size: 0.72rem; opacity: 0.45; border: none; background: transparent;
      cursor: pointer; padding: 1px 4px; vertical-align: middle;
      transition: opacity 0.15s;
    }}
    .permalink-btn:hover {{ opacity: 1; }}

    /* ─── Floating / PiP video player ─── */
    .pip-container {{
      position: fixed; top: 16px; right: 16px;
      width: clamp(260px, 33vw, 720px); z-index: 900;
      background: #111; border-radius: 10px;
      box-shadow: 0 6px 32px rgba(0,0,0,0.55); overflow: hidden;
    }}
    .pip-titlebar {{
      display: flex; align-items: center; justify-content: space-between;
      padding: 5px 10px; background: rgba(255,255,255,0.07);
      font-size: 0.75rem; color: #ccc;
    }}
    .pip-close {{
      background: none; border: none; color: #aaa; cursor: pointer;
      font-size: 1rem; padding: 0 4px; line-height: 1;
    }}
    .pip-close:hover {{ color: #fff; }}
    .pip-container video {{ width: 100%; display: block; }}
    .pip-launch {{
      font-size: 0.72rem; padding: 1px 6px; background: transparent;
      border: 1px solid var(--accent); color: var(--accent); border-radius: 4px;
      cursor: pointer; white-space: nowrap;
    }}
    .pip-launch:hover {{ background: var(--accent); color: #fff; }}

    /* ─── Code-at-line modal ─── */
    .code-line {{ display: block; padding: 0 4px; line-height: 1.55; }}
    .code-line.hl-line {{
      background: rgba(252,211,77,0.18);
      border-left: 3px solid var(--warning);
      padding-left: 8px;
    }}
    .code-line-num {{
      display: inline-block; min-width: 3em; color: var(--text3);
      user-select: none; text-align: right; margin-right: 10px;
      font-size: 0.8em; opacity: 0.6;
    }}
    /* Prevent hljs theme from recolouring line numbers */
    .code-line .code-line-num {{ color: var(--text3) !important; opacity: 0.6 !important; }}

    /* ─── Responsive ─── */
    @media (max-width: 640px) {{
      .header {{ padding: 16px; }}
      .main {{ padding: 12px 12px 80px; }}
      .stats-bar {{ padding: 10px 16px; gap: 12px; }}
      .course-header {{ padding: 14px 16px; }}
      .course-meta {{ display: none; }}
    }}
  </style>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
  <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
  <script src="https://cdn.socket.io/4.7.5/socket.io.min.js" onerror="window._sioMissing=true"></script>
</head>
<body>

<!-- ─── Header ─── -->
<div class="header">
  <div class="header-top">
    <div>
      <div class="header-title">📚 Course Catalog</div>
      <div class="header-subtitle">Generated {gen_date} · {len(courses)} courses · {total_files} files · {html_escape(total_size)}</div>
    </div>
    <div class="header-actions">
      <button class="btn theme-toggle" onclick="toggleTheme()" title="Toggle theme">🌙</button>
      <button class="btn" onclick="expandAll()">⬇ Expand All</button>
      <button class="btn" onclick="collapseAll()">⬆ Collapse All</button>
    </div>
  </div>
  <div class="search-bar">
    <input type="text" id="searchInput" placeholder="🔍  Search courses and files..."
           oninput="handleSearch(this.value)" autocomplete="off">
    <span class="search-icon" id="searchIcon">⌕</span>
    <span class="search-spinner" id="searchSpinner">↻</span>
  </div>
  <div class="search-opts-row">
    <button class="search-opts-btn" id="searchOptsBtn" onclick="toggleSearchOpts()" title="Search options">
      ⚙ Search Options<span class="search-opts-badge" id="searchOptsBadge">ON</span>
    </button>
  </div>
  <div class="streaming-log" id="streamingLog">
    <span class="streaming-log-text" id="streamingLogText">🔍 Searching...</span>
    <button class="streaming-cancel-btn" onclick="cancelStreaming()">✕ Cancel</button>
  </div>
  <div class="search-opts-panel" id="searchOptsPanel">
    <!-- Section: Backend options -->
    <div class="sopt-section">
      <div class="sopt-section-title">Backend options</div>
      <label class="sopt-label">
        🌐
        <select id="searchLang" onchange="onSearchOptChange(); _updateSoundexState()">
          <option value="">— Language —</option>
          <option value="de">🇩🇪 Deutsch</option>
          <option value="es">🇪🇸 Español</option>
          <option value="en">🇬🇧 English</option>
        </select>
      </label>
      <label class="sopt-label sopt-check" id="soundexLabel" title="Phonetic matching — requires a language to be selected">
        <input type="checkbox" id="searchSoundex" onchange="onSearchOptChange()" disabled>
        🔊 Soundex
      </label>
      <label class="sopt-label sopt-check" title="Distinguish uppercase from lowercase">
        <input type="checkbox" id="searchCS" onchange="onSearchOptChange()">
        Aa Case sensitive
      </label>
      <label class="sopt-label sopt-check" title="Phonetic filename matching in local search (uses selected language)">
        <input type="checkbox" id="searchSoundexFiles" onchange="onSearchOptChange()">
        🔊 Filenames
      </label>
      <label class="sopt-label sopt-check" title="Use pre-built text cache for faster searches (files already converted to text)">
        <input type="checkbox" id="searchCached" onchange="onSearchOptChange()">
        ⚡ Cached
      </label>
      <label class="sopt-label sopt-check" title="Checked = WebSocket streaming (real-time results); unchecked = REST API (results after full scan)">
        <input type="checkbox" id="searchStreaming" onchange="onSearchOptChange()" checked>
        ⚡ WebSocket <small style="opacity:.65">(uncheck → REST)</small>
      </label>
      <button class="sopt-cache-btn" id="buildCacheBtn" onclick="buildCache()" title="Pre-convert all files to text cache for faster future searches">
        🗂 Build Cache
      </button>
      <span class="sopt-hint" id="searchOptsHint"></span>
    </div>
    <!-- Section: File types -->
    <div class="sopt-section">
      <div class="sopt-section-title">File types</div>
      <div class="sopt-filetypes" id="soptFileTypes">
        <span style="color:var(--text3);font-size:0.75rem">Connect to backend to load file types</span>
      </div>
    </div>
  </div>
  <div class="search-count" id="searchCount"></div>
  <div class="backend-results" id="backendResults">
    <div class="backend-results-header" onclick="toggleBackendResults()">
      <span class="backend-chevron" id="backendChevron">▶</span>
      <span id="backendResultsTitle">🔍 Backend search results</span>
      <span class="backend-results-close" onclick="event.stopPropagation();closeBackendResults()">✕</span>
    </div>
    <div class="backend-results-body" id="backendResultsBody" style="display:none"></div>
  </div>
</div>

<!-- ─── Stats Bar ─── -->
<div class="stats-bar">
  <div class="stat-item">🎓 <strong>{len(courses)}</strong> Courses</div>
  <div class="stat-item">📁 <strong>{total_files}</strong> Files</div>
  <div class="stat-item">💾 <strong>{html_escape(total_size)}</strong> Total</div>
</div>

<!-- ─── Main ─── -->
<div class="main">
  <div class="toolbar">
    <div class="filter-btns" id="filterBtns">
      <button class="filter-btn active" onclick="filterCat('all', this)">All</button>
      <button class="filter-btn" onclick="filterCat('video', this)">🎬 Video</button>
      <button class="filter-btn" onclick="filterCat('pdf', this)">📄 PDF</button>
      <button class="filter-btn" onclick="filterCat('image', this)">🖼️ Image</button>
      <button class="filter-btn" onclick="filterCat('audio', this)">🎵 Audio</button>
      <button class="filter-btn" onclick="filterCat('text', this)">📋 Code/Text</button>
      <button class="filter-btn" onclick="filterCat('office', this)">📁 Office</button>
      <button class="filter-btn" onclick="filterCat('archive', this)">📦 Archive</button>
      <button class="filter-btn" onclick="filterCat('elodms', this)">📦 ELO</button>
    </div>
  </div>

  <div id="courseList">
{courses_html}
  </div>
  <div id="noResults" style="display:none; text-align:center; padding:60px; color:var(--text3);">
    <div style="font-size:3rem;margin-bottom:12px">🔍</div>
    <div style="font-size:1.1rem">No courses or files matched your search.</div>
  </div>
</div>

<!-- ─── Modal ─── -->
<div class="modal-overlay" id="modalOverlay" onclick="closeModal(event)">
  <div class="modal" id="modal">
    <div class="modal-header">
      <span class="modal-title" id="modalTitle">File Preview</span>
      <button class="modal-maximize" onclick="toggleModalMaximize()" title="Toggle fullscreen">⛶</button>
      <button class="modal-close" onclick="closeModalDirect()">✕</button>
    </div>
    <div class="modal-body" id="modalBody"></div>
  </div>
</div>

<!-- ─── Notes Panel ─── -->
<div class="notes-panel">
  <div class="notes-panel-popup" id="notesPanelPopup">
    <h3>📝 My Course Notes</h3>
    <div class="notes-count" id="notesCount">No notes saved yet.</div>
    <button class="btn btn-accent" style="width:100%;margin-bottom:8px" onclick="exportNotes()">⬇ Export notes.txt</button>
    <button class="btn" style="width:100%;color:var(--danger)" onclick="clearAllNotes()">🗑 Clear All Notes</button>
  </div>
  <button class="notes-panel-btn" onclick="toggleNotesPanel()" title="Notes">📝</button>
</div>

<!-- ─── Toast ─── -->
<div class="toast" id="toast"></div>

<script>
// ─── Backend Search ───────────────────────────────────────
const BACKEND_URL = 'http://localhost:5000';
let backendSearchTimer = null;
let _socket = null;
let _streamingActive = false;
let _streamingResultIdx = 0;
let _pendingQuery = '';
let _seenTypes = new Set();
let backendResultsOpen = false;

// ─── Socket.IO streaming search ──────────────────────────────────────────────

function _initSocket() {{
  if (_socket) return _socket;
  if (typeof io === 'undefined' || window._sioMissing) return null;
  try {{
    _socket = io(BACKEND_URL, {{ transports: ['websocket', 'polling'], reconnection: true }});
    _socket.on('search_progress', (data) => {{
      const pct = data.total ? `[${{data.idx}}/${{data.total}}]` : `[${{data.idx}}]`;
      const el = document.getElementById('streamingLogText');
      if (el) el.textContent = `🔍 Searching ${{pct}} ${{data.folder}}`;
    }});
    _socket.on('search_result', (result) => {{
      _appendStreamingResult(result);
    }});
    _socket.on('search_done', (data) => {{
      _streamingActive = false;
      _pendingQuery = '';
      _hideStreamingLog();
      showBackendSpinner(false);
      const title = document.getElementById('backendResultsTitle');
      if (title) {{
        const elapsed = data.elapsed_s !== undefined ? ` · ${{data.elapsed_s}}s` : '';
        const cancelled = data.cancelled ? ' (cancelled)' : '';
        title.textContent = `🔍 Backend: ${{data.total}} match(es) in ${{data.result_count}} file(s)${{elapsed}}${{cancelled}}`;
      }}
    }});
    _socket.on('connect_error', () => {{
      const wasActive = _streamingActive;
      const fallbackQuery = _pendingQuery;
      _streamingActive = false;
      _pendingQuery = '';
      _hideStreamingLog();
      showBackendSpinner(false);
      _socket = null;  // reset so next attempt tries fresh connection
      if (wasActive && fallbackQuery) {{
        console.warn('WebSocket unavailable — falling back to REST API');
        backendSearchREST(fallbackQuery);
      }}
    }});
    return _socket;
  }} catch(e) {{ return null; }}
}}

function _showStreamingLog(text) {{
  const el = document.getElementById('streamingLog');
  if (el) {{ el.classList.add('active'); document.getElementById('streamingLogText').textContent = text || '🔍 Searching...'; }}
}}
function _hideStreamingLog() {{
  const el = document.getElementById('streamingLog');
  if (el) el.classList.remove('active');
}}

function cancelStreaming() {{
  if (_socket && _streamingActive) _socket.emit('search_cancel');
  _streamingActive = false;
  _hideStreamingLog();
  showBackendSpinner(false);
}}

function _resetBackendPanel() {{
  const panel = document.getElementById('backendResults');
  const body  = document.getElementById('backendResultsBody');
  const title = document.getElementById('backendResultsTitle');
  _seenTypes.clear();
  _streamingResultIdx = 0;
  if (body)  body.innerHTML = '<div class="backend-type-filters" id="streamTypeFilters">'
    + '<button class="backend-type-btn active" onclick="filterBackendType(\\'all\\',this)">All</button>'
    + '</div>';
  if (title) title.textContent = '🔍 Searching\u2026';
  if (panel) {{ panel.style.display = 'block'; }}
  if (backendResultsOpen) toggleBackendResults();
}}

function _appendStreamingResult(result) {{
  const body = document.getElementById('backendResultsBody');
  if (!body) return;
  const ri = _streamingResultIdx++;
  const query = document.getElementById('searchInput')?.value?.trim() || '';
  // Add type filter button if new type
  if (!_seenTypes.has(result.type)) {{
    _seenTypes.add(result.type);
    const filterBar = document.getElementById('streamTypeFilters');
    if (filterBar) {{
      const btn = document.createElement('button');
      btn.className = 'backend-type-btn';
      btn.dataset.btype = result.type;
      btn.onclick = function() {{ filterBackendType(result.type, this); }};
      btn.innerHTML = getBackendFileIcon(result.type) + ' ' + result.type.toUpperCase();
      filterBar.appendChild(btn);
    }}
  }}
  const wrapper = document.createElement('div');
  wrapper.innerHTML = _buildResultHTML(result, ri, query);
  body.appendChild(wrapper.firstElementChild);
  if (ri === 0 && !backendResultsOpen) toggleBackendResults();
}}

function _buildResultHTML(r, ri, query) {{
  const _videoExts = new Set(['mp4','mkv','avi','mov','webm','m4v','flv','wmv','ts','mpg','mpeg']);
  const _textTypes = new Set(['md','txt','py','js','ts','css','json','xml','yaml','yml','toml','ini',
    'sh','bash','sql','java','cpp','c','h','cs','go','rs','rb','php',
    'log','conf','config','csv','srt','vtt','properties','html','htm']);
  const qRe = query ? new RegExp(escapeRegex(query), 'gi') : null;
  const matchesHtml = (r.matches || []).map((m, _mi) => {{
    const ctx = (m.context || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    const hi = qRe ? ctx.replace(qRe, s => `<mark>${{s}}</mark>`) : ctx;
    const pageBadge = m.page ? `<span class="backend-match-page" title="Page ${{m.page}}">p.${{m.page}}</span>` : '';
    const openPageBtn = (m.page && r.type === 'pdf')
      ? `<button class="srt-play-btn" onclick="openPDFAtPage('${{escAttr(r.path)}}','${{escAttr(r.file)}}',${{m.page}})" title="Open at page ${{m.page}}">📄 p.${{m.page}}</button>` : '';
    const playBtn = (m.srt_time !== undefined)
      ? `<button class="srt-play-btn" onclick="playSrt('${{escAttr(r.path)}}',${{m.srt_time}})" title="Play at ${{formatSrtTime(m.srt_time)}}">▶ Play ${{formatSrtTime(m.srt_time)}}</button>` : '';
    const openLineBtn = (!m.page && m.srt_time === undefined && _textTypes.has(r.type))
      ? `<button class="srt-play-btn" onclick="openFromBackendAtLine('${{escAttr(r.path)}}','${{escAttr(r.file)}}','${{escAttr(r.type)}}',${{m.line}},${{_mi+1}})" title="Open at line ${{m.line}}">${{r.type==='html'||r.type==='htm'?'🌐':'📋'}} L.${{m.line}}</button>` : '';
    return `<div class="backend-match-line"><span class="backend-match-linenum">L${{m.line}}:</span>${{pageBadge}}${{hi}}${{playBtn}}${{openPageBtn}}${{openLineBtn}}</div>`;
  }}).join('');
  const ficon = getBackendFileIcon(r.type);
  const isVideo = _videoExts.has(r.type);
  const vlcBtn = isVideo
    ? `<button class="backend-result-open-btn" style="background:var(--warning);color:#000;margin-left:6px" onclick="openVlcVideo('${{escAttr(r.path)}}')">▶ VLC</button>` : '';
  const srcBtn = (r.type === 'html' || r.type === 'htm')
    ? `<button class="backend-result-open-btn" style="margin-left:4px" onclick="openHtmlSource('${{escAttr(r.path)}}','${{escAttr(r.file)}}')" title="View HTML source code">📋 Source</button>`
    : '';
  return `<div class="backend-result-item" data-btype="${{r.type}}">
    <div class="backend-result-header" onclick="toggleBackendResult(${{ri}})">
      <span>${{ficon}}</span>
      <span class="backend-result-file">${{escHtml(r.file)}}<span class="backend-result-path">${{escHtml(r.rel_path||r.path)}}</span></span>
      <span class="backend-result-count">${{r.match_count}} match(es)</span>
    </div>
    <div class="backend-result-matches" id="bres-${{ri}}">${{matchesHtml}}
      <button class="backend-result-open-btn" onclick="openFromBackend('${{escAttr(r.path)}}','${{escAttr(r.file)}}','${{escAttr(r.type)}}')">📂 Open file</button>${{srcBtn}}${{vlcBtn}}
    </div></div>`;
}}

function streamingSearch(query) {{
  if (!query || query.length < 2) {{ closeBackendResults(); return; }}
  const sock = _initSocket();
  if (!sock) {{ backendSearchREST(query); return; }}
  if (_streamingActive) sock.emit('search_cancel');
  _streamingActive = true;
  _pendingQuery = query;
  showBackendSpinner(true);
  _resetBackendPanel();
  _showStreamingLog('🔍 Connecting\u2026');
  const {{lang, soundex, cs, cached, types}} = _getSearchOpts();
  sock.emit('search', {{ q: query, lang: lang||'', soundex: soundex?1:0, cs: cs?1:0, cached: cached?1:0, types: types||'' }});
}}

// ─── Backend health check ─────────────────────────────────
let _backendOnline = false;
function _checkBackend() {{
  const ctrl = new AbortController();
  const tid = setTimeout(() => ctrl.abort(), 2500);
  fetch(BACKEND_URL + '/api/health', {{ signal: ctrl.signal }})
    .then(r => {{ clearTimeout(tid); return r.ok ? r.json() : null; }})
    .then(data => {{ if (data) {{ _backendOnline = true; _onBackendOnline(data); }} }})
    .catch(() => {{ clearTimeout(tid); }});
}}
function _onBackendOnline(data) {{
  document.querySelectorAll('.vlc-launch').forEach(el => el.style.removeProperty('display'));
  if (data && data.supported_extensions) _loadFileTypes(data.supported_extensions);
}}

function _loadFileTypes(exts) {{
  const container = document.getElementById('soptFileTypes');
  if (!container) return;
  // Buttons: select all / none
  let html = '<button class="sopt-ft-all-btn" onclick="_ftSelectAll(true)">All</button>'
           + '<button class="sopt-ft-none-btn" onclick="_ftSelectAll(false)">None</button>';
  exts.forEach(ext => {{
    html += `<label class="sopt-ft-label" title=".${{ext}}">
      <input type="checkbox" name="ft" value="${{ext}}" checked onchange="onSearchOptChange()">
      ${{ext}}
    </label>`;
  }});
  container.innerHTML = html;
}}

function _ftSelectAll(checked) {{
  document.querySelectorAll('#soptFileTypes input[name="ft"]').forEach(cb => {{ cb.checked = checked; }});
  onSearchOptChange();
}}

function _getSelectedFileTypes() {{
  const all = [...document.querySelectorAll('#soptFileTypes input[name="ft"]')];
  if (all.length === 0) return '';  // no types loaded → no filter
  const checked = all.filter(cb => cb.checked).map(cb => cb.value);
  if (checked.length === all.length) return '';  // all selected → no filter needed
  return checked.join(',');
}}

function buildCache() {{
  const btn = document.getElementById('buildCacheBtn');
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  btn.textContent = '⏳ Building…';
  fetch(BACKEND_URL + '/api/cache', {{ method: 'POST' }})
    .then(r => r.json())
    .then(data => {{
      btn.disabled = false;
      btn.textContent = '🗂 Build Cache';
      showToast(`Cache built: ${{data.processed}} new, ${{data.skipped}} up-to-date, ${{data.errors}} errors (${{data.elapsed_s}}s)`);
    }})
    .catch(() => {{
      btn.disabled = false;
      btn.textContent = '🗂 Build Cache';
      showToast('Cache build failed — backend not reachable');
    }});
}}
// Hide VLC buttons until backend confirmed alive
document.addEventListener('DOMContentLoaded', () => {{
  document.querySelectorAll('.vlc-launch').forEach(el => el.style.display = 'none');
  _checkBackend();
  // Handle deep-link ?doc= param
  _handleDocParam();
}});

function toggleBackendResults() {{
  const body = document.getElementById('backendResultsBody');
  const chev = document.getElementById('backendChevron');
  backendResultsOpen = !backendResultsOpen;
  body.style.display = backendResultsOpen ? 'block' : 'none';
  chev.classList.toggle('open', backendResultsOpen);
}}

function closeBackendResults() {{
  document.getElementById('backendResults').style.display = 'none';
  backendResultsOpen = false;
}}

function showBackendSpinner(show) {{
  document.getElementById('searchSpinner').style.display = show ? 'inline' : 'none';
  document.getElementById('searchIcon').style.display = show ? 'none' : 'inline';
}}

// ─── Search Options ───────────────────────────────────────
function toggleSearchOpts() {{
  const panel = document.getElementById('searchOptsPanel');
  const btn   = document.getElementById('searchOptsBtn');
  const open  = panel.classList.toggle('open');
  btn.classList.toggle('active', open);
}}

function _getSearchOpts() {{
  const lang    = document.getElementById('searchLang')?.value || '';
  const soundex = document.getElementById('searchSoundex')?.checked || false;
  const cs      = document.getElementById('searchCS')?.checked || false;
  const cached  = document.getElementById('searchCached')?.checked || false;
  const types   = _getSelectedFileTypes();
  return {{ lang, soundex, cs, cached, types }};
}}

function _updateOptsBadge() {{
  const {{ lang, soundex, cs, cached, types }} = _getSearchOpts();
  const active = !!(lang || soundex || cs || cached || types);
  document.getElementById('searchOptsBtn')?.classList.toggle('active',
    active || document.getElementById('searchOptsPanel')?.classList.contains('open'));
  document.getElementById('searchOptsBadge').style.display = active ? 'inline' : 'none';
  const parts = [
    lang   ? ({{'de':'Deutsch','es':'Español','en':'English'}}[lang] || lang) : '',
    soundex ? 'Soundex' : '',
    cs      ? 'Case-sensitive' : '',
    cached  ? '⚡ Cached' : '',
    types   ? `Types: ${{types}}` : '',
  ].filter(Boolean);
  document.getElementById('searchOptsHint').textContent =
    active ? ('Active: ' + parts.join(' · ')) : '';
}}

function _updateSoundexState() {{
  const lang = document.getElementById('searchLang')?.value || '';
  const cb   = document.getElementById('searchSoundex');
  const lbl  = document.getElementById('soundexLabel');
  if (!lang) {{
    cb.checked = false;
    cb.disabled = true;
    lbl.style.opacity = '0.45';
    lbl.title = 'Phonetic matching — requires a language to be selected';
  }} else {{
    cb.disabled = false;
    lbl.style.opacity = '';
    lbl.title = 'Phonetic matching for ' + lang.toUpperCase();
  }}
  _updateOptsBadge();
}}

function onSearchOptChange() {{
  _updateOptsBadge();
  const query = document.getElementById('searchInput').value.trim();
  if (query.length >= 2) {{
    clearTimeout(backendSearchTimer);
    backendSearchTimer = setTimeout(() => backendSearch(query), 3000);
  }}
}}

function backendSearchREST(query) {{
  if (!query || query.length < 2) {{ closeBackendResults(); return; }}
  const {{ lang, soundex, cs, cached, types }} = _getSearchOpts();
  let url = BACKEND_URL + '/api/search?q=' + encodeURIComponent(query);
  if (lang)    url += '&lang='    + encodeURIComponent(lang);
  if (soundex) url += '&soundex=1';
  if (cs)      url += '&cs=1';
  if (cached)  url += '&cached=1';
  if (types)   url += '&types='  + encodeURIComponent(types);
  showBackendSpinner(true);
  fetch(url)
    .then(r => r.json())
    .then(data => {{ showBackendSpinner(false); renderBackendResults(data, query); }})
    .catch(() => {{ showBackendSpinner(false); }});
}}

function backendSearch(query) {{
  if (!query || query.length < 2) {{ closeBackendResults(); return; }}
  const useStreaming = document.getElementById('searchStreaming')?.checked !== false;
  if (useStreaming && typeof io !== 'undefined' && !window._sioMissing) {{
    streamingSearch(query);
  }} else {{
    backendSearchREST(query);
  }}
}}

function renderBackendResults(data, query) {{
  const panel = document.getElementById('backendResults');
  const body = document.getElementById('backendResultsBody');
  const title = document.getElementById('backendResultsTitle');
  if (!data.results || data.results.length === 0) {{ panel.style.display = 'none'; return; }}
  const _m = data.mode || {{}};
  const _modeParts = [
    _m.lang ? ({{'de':'Deutsch','es':'Español','en':'English'}}[_m.lang] || _m.lang) : '',
    _m.soundex ? '🔊 Soundex' : '',
    _m.cs      ? 'Aa Case-sensitive' : '',
  ].filter(Boolean);
  const _modeStr = _modeParts.length ? ' · ' + _modeParts.join(' + ') : '';
  const _elapsed = data.elapsed_s !== undefined ? ` · ${{data.elapsed_s}}s` : '';
  title.textContent = `🔍 Backend: ${{data.total}} match(es) in ${{data.results.length}} file(s)${{_modeStr}}${{_elapsed}}`;
  panel.style.display = 'block';

  // Build type filter buttons from unique types in results
  const _rtypes = [...new Set(data.results.map(r => r.type))].sort();
  let typeHtml = '<div class="backend-type-filters"><button class="backend-type-btn active" onclick="filterBackendType(\\'all\\',this)">All</button>';
  _rtypes.forEach(t => {{
    typeHtml += `<button class="backend-type-btn" data-btype="${{t}}" onclick="filterBackendType('${{t}}',this)">${{getBackendFileIcon(t)}} ${{t.toUpperCase()}}</button>`;
  }});
  typeHtml += '</div>';

  let html = '';
  data.results.forEach((r, ri) => {{
    html += _buildResultHTML(r, ri, query);
  }});
  body.innerHTML = typeHtml + html;
  if (!backendResultsOpen) toggleBackendResults();
}}

function toggleBackendResult(ri) {{
  const el = document.getElementById('bres-' + ri);
  if (el) el.classList.toggle('open');
}}

function filterBackendType(type, btn) {{
  document.querySelectorAll('.backend-type-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.backend-result-item').forEach(item => {{
    item.style.display = (type === 'all' || item.dataset.btype === type) ? '' : 'none';
  }});
}}

function openVlcVideo(path) {{
  fetch(BACKEND_URL + '/api/play-video?path=' + encodeURIComponent(path))
    .then(r => r.json())
    .then(d => {{ if (d.error) alert('⚠ ' + d.error); else showToast('▶ Launching VLC...'); }})
    .catch(() => alert('⚠ Could not connect to backend'));
}}

function openPDFAtPage(path, name, page) {{
  const url = BACKEND_URL + '/api/file?path=' + encodeURIComponent(path) + '#page=' + page;
  setModalTitle(name + ' — p.' + page, 'file:///' + path.replace(/\\\\/g, '/'));
  document.getElementById('modalBody').innerHTML = `<iframe src="${{url}}" title="PDF p.${{page}}"></iframe>`;
  document.getElementById('modal').classList.remove('maximized');
  document.getElementById('modalOverlay').classList.add('open');
}}

function openFromBackendAtLine(path, name, type, lineNum, occurrenceIdx) {{
  const directUrl = 'file:///' + path.replace(/\\\\/g, '/');
  // HTML/HTM: render in modal iframe and scroll to matched text
  if (type === 'html' || type === 'htm') {{
    const _q = document.getElementById('searchInput')?.value?.trim() || '';
    setModalTitle(name + ' — L.' + lineNum, directUrl);
    const body = document.getElementById('modalBody');
    body.innerHTML = '<div style="padding:16px;color:var(--text3)">Loading…</div>';
    body.style.padding = '0';
    document.getElementById('modal').classList.add('maximized');
    document.getElementById('modalOverlay').classList.add('open');
    fetch(BACKEND_URL + '/api/file-text?path=' + encodeURIComponent(path) + '&raw=1')
      .then(r => r.json())
      .then(d => {{
        let html = d.content || '';
        // Inject <base> so relative resources (images, css) resolve correctly
        const fileDir = directUrl.replace(/[^/\\\\]*$/, '');
        const baseTag = '<base href="' + fileDir + '">';
        if (/<head[^>]*>/i.test(html)) {{
          html = html.replace(/(<head[^>]*>)/i, '$1' + baseTag);
        }} else {{
          html = baseTag + html;
        }}
        // Build scroll script — runs inside the iframe's own window
        const _n = occurrenceIdx || 1;
        const scrollJs =
          'window.addEventListener("DOMContentLoaded",function(){{' +
            'var q=' + JSON.stringify(_q) + ';' +
            'var n=' + _n + ';' +
            'if(!q)return;' +
            'for(var i=0;i<n;i++){{window.find(q,false,false,true);}}' +
            'var sel=window.getSelection();' +
            'if(sel&&sel.rangeCount){{' +
              'var el=sel.getRangeAt(0).startContainer.parentElement;' +
              'if(el){{' +
                'el.style.outline="3px solid #ffeb3b";' +
                'el.scrollIntoView({{block:"center"}});' +
              '}}' +
            '}}' +
          '}});';
        const scrollScript = '<scr' + 'ipt>' + scrollJs + '<\\/' + 'script>';
        if (/<\\/body>/i.test(html)) {{
          html = html.replace(/<\\/body>/i, scrollScript + '</body>');
        }} else {{
          html += scrollScript;
        }}
        _buildHtmlViewer(body, d.content || '', html);
      }})
      .catch(() => {{ body.style.padding = ''; closeModalDirect(); window.open(directUrl, '_blank'); }});
    return;
  }}
  // SRT: open SRT viewer in modal
  if (type === 'srt') {{
    setModalTitle(name, directUrl);
    document.getElementById('modalBody').innerHTML = '<pre>Loading...</pre>';
    document.getElementById('modal').classList.remove('maximized');
    document.getElementById('modalOverlay').classList.add('open');
    fetch(BACKEND_URL + '/api/file-text?path=' + encodeURIComponent(path))
      .then(r => r.json())
      .then(d => {{
        const body = document.getElementById('modalBody');
        body.innerHTML = '';
        body.appendChild(renderSRTViewer(d.content || '', path));
      }})
      .catch(() => {{ closeModalDirect(); window.open(directUrl, '_blank'); }});
    return;
  }}
  // Text/code files: open in modal and scroll to target line
  setModalTitle(name + ' — L.' + lineNum, directUrl);
  document.getElementById('modalBody').innerHTML = '<pre style="padding:8px;margin:0">Loading...</pre>';
  document.getElementById('modal').classList.remove('maximized');
  document.getElementById('modalOverlay').classList.add('open');
  fetch(BACKEND_URL + '/api/file-text?path=' + encodeURIComponent(path))
    .then(r => r.json())
    .then(d => {{
      // Strip leading blank lines and adjust target line accordingly
      const allLines = (d.content || '').split('\\n');
      let stripped = 0;
      while (stripped < allLines.length && allLines[stripped].trim() === '') stripped++;
      const content = stripped > 0 ? allLines.slice(stripped).join('\\n') : (d.content || '');
      const targetLine = Math.max(1, lineNum - stripped);
      const pre = renderCodeLines(content, d.language || type, targetLine, stripped);
      const body = document.getElementById('modalBody');
      body.innerHTML = '';
      body.appendChild(pre);
      setTimeout(() => {{
        const target = document.getElementById('cL' + targetLine);
        if (target) {{
          const tRect = target.getBoundingClientRect();
          const bRect = body.getBoundingClientRect();
          body.scrollTop += tRect.top - bRect.top - body.clientHeight / 2 + tRect.height / 2;
        }}
      }}, 120);
    }})
    .catch(() => {{ closeModalDirect(); window.open(directUrl, '_blank'); }});
}}

function setModalTitle(name, url) {{
  const titleEl = document.getElementById('modalTitle');
  if (url) {{
    titleEl.innerHTML = '<a href="' + escHtml(url) + '" target="_blank" rel="noopener">' + escHtml(name) + ' ↗</a>';
  }} else {{
    titleEl.textContent = name;
  }}
}}

function openFromBackend(path, name, type) {{
  const url = BACKEND_URL + '/api/file?path=' + encodeURIComponent(path);
  const directUrl = 'file:///' + encodeURI(path.replace(/\\\\/g, '/'));
  setModalTitle(name, directUrl);
  if (type === 'pdf') {{
    document.getElementById('modalBody').innerHTML = `<iframe src="${{url}}" title="PDF"></iframe>`;
    document.getElementById('modalOverlay').classList.add('open');
  }} else if (type === 'html' || type === 'htm') {{
    const body = document.getElementById('modalBody');
    body.innerHTML = '<div style="padding:16px;color:var(--text3)">Loading\u2026</div>';
    body.style.padding = '0';
    document.getElementById('modal').classList.add('maximized');
    document.getElementById('modalOverlay').classList.add('open');
    fetch(BACKEND_URL + '/api/file-text?path=' + encodeURIComponent(path) + '&raw=1')
      .then(r => r.json())
      .then(d => {{
        let html = d.content || '';
        const fileDir = directUrl.replace(/[^/\\\\]*$/, '');
        const baseTag = '<base href="' + fileDir + '">';
        if (/<head[^>]*>/i.test(html)) {{
          html = html.replace(/(<head[^>]*>)/i, '$1' + baseTag);
        }} else {{
          html = baseTag + html;
        }}
        _buildHtmlViewer(body, d.content || '', html);
      }})
      .catch(() => {{ body.style.padding = ''; closeModalDirect(); window.open(directUrl, '_blank'); }});
  }} else if (['js','ts','py','java','cpp','c','h','cs','go','rs','rb','php',
               'sql','sh','json','xml','yaml','yml','css','md','txt','srt','csv'].includes(type)) {{
    const body = document.getElementById('modalBody');
    body.innerHTML = '<pre style="padding:8px;margin:0">Loading\u2026</pre>';
    body.style.padding = '';
    document.getElementById('modal').classList.remove('maximized');
    document.getElementById('modalOverlay').classList.add('open');
    fetch(BACKEND_URL + '/api/file-text?path=' + encodeURIComponent(path))
      .then(r => r.json())
      .then(d => {{
        body.innerHTML = '';
        if (type === 'srt') {{
          body.appendChild(renderSRTViewer(d.content || '', path));
        }} else {{
          body.appendChild(renderCodeLines(d.content || '', d.language || type, 0, 0));
        }}
      }})
      .catch(err => {{
        body.innerHTML = '<div style="padding:16px;color:var(--warning)">Error loading file: ' + escHtml(String(err)) + '</div>';
      }});
  }} else {{
    window.open(url, '_blank');
  }}
}}

// ─── Open HTML file as source code ───────────────────────
function openHtmlSource(path, name) {{
  const directUrl = 'file:///' + encodeURI(path.replace(/\\\\/g, '/'));
  setModalTitle(name + ' \u2014 source', directUrl);
  const body = document.getElementById('modalBody');
  body.innerHTML = '<pre style="padding:8px;margin:0">Loading\u2026</pre>';
  body.style.padding = '';
  document.getElementById('modal').classList.remove('maximized');
  document.getElementById('modalOverlay').classList.add('open');
  fetch(BACKEND_URL + '/api/file-text?path=' + encodeURIComponent(path) + '&raw=1')
    .then(r => r.json())
    .then(d => {{
      body.innerHTML = '';
      body.appendChild(renderCodeLines(d.content || '', 'html', 0, 0));
    }})
    .catch(err => {{
      body.innerHTML = '<div style="padding:16px;color:var(--warning)">Error loading source: ' + escHtml(String(err)) + '</div>';
    }});
}}

// ─── Code formatter helpers ───────────────────────────────
function formatXml(xml) {{
  const INDENT = '  ';
  xml = xml.replace(/>\\s*</g, '><');
  let pad = 0;
  return xml
    .split(/(<[^>]+>)/)
    .filter(s => s.trim())
    .map(node => {{
      if (/^<\\/\\w/.test(node)) {{ pad = Math.max(0, pad - 1); }}
      const line = INDENT.repeat(pad) + node;
      if (/^<[^?!\\/][^>]*[^\\/]>$/.test(node)) {{ pad++; }}
      return line;
    }})
    .join('\\n');
}}

function prepareCodeContent(content, type) {{
  if (type === 'json') {{
    try {{ return JSON.stringify(JSON.parse(content), null, 2); }} catch(e) {{}}
  }} else if (type === 'xml') {{
    try {{ return formatXml(content); }} catch(e) {{}}
  }}
  return content;
}}

function renderCodeLines(content, language, targetLine, lineOffset) {{
  const formatted = prepareCodeContent(content, language);
  const rawLines = formatted.split('\\n');
  const offset = lineOffset || 0;
  const pre = document.createElement('pre');
  pre.style.cssText = 'margin:0;padding:8px;overflow:auto';
  const code = document.createElement('code');
  code.className = 'hljs language-' + language;
  let hlLines = null;
  if (window.hljs) {{
    try {{
      const lang = hljs.getLanguage(language) ? language : 'plaintext';
      hlLines = hljs.highlight(formatted, {{language: lang}}).value.split('\\n');
    }} catch(e) {{}}
  }}
  rawLines.forEach((rawLn, i) => {{
    const lineNum = i + 1;
    const span = document.createElement('span');
    span.id = 'cL' + lineNum;
    span.className = 'code-line' + (lineNum === targetLine ? ' hl-line' : '');
    const numSpan = document.createElement('span');
    numSpan.className = 'code-line-num';
    numSpan.textContent = lineNum + offset;
    span.appendChild(numSpan);
    if (hlLines) {{
      const lineContent = document.createElement('span');
      lineContent.innerHTML = hlLines[i] !== undefined ? hlLines[i] : '';
      span.appendChild(lineContent);
    }} else {{
      span.appendChild(document.createTextNode(rawLn));
    }}
    code.appendChild(span);
  }});
  pre.appendChild(code);
  return pre;
}}

// ─── HTML viewer: rendered page + toggle to source ────────
function _buildHtmlViewer(body, rawContent, iframeSrcdoc) {{
  let showingSource = false;
  const iframe = document.createElement('iframe');
  iframe.style.cssText = 'width:100%;border:none;display:block';
  iframe.srcdoc = iframeSrcdoc;

  const wrap = document.createElement('div');
  wrap.className = 'html-viewer-wrap';

  const toolbar = document.createElement('div');
  toolbar.className = 'html-viewer-toolbar';

  const renderedBtn = document.createElement('button');
  renderedBtn.className = 'html-view-btn active';
  renderedBtn.textContent = '🌐 Rendered';

  const sourceBtn = document.createElement('button');
  sourceBtn.className = 'html-view-btn';
  sourceBtn.textContent = '📋 Source';

  const contentDiv = document.createElement('div');
  contentDiv.className = 'html-viewer-content';
  contentDiv.appendChild(iframe);

  renderedBtn.onclick = function() {{
    if (!showingSource) return;
    showingSource = false;
    renderedBtn.classList.add('active');
    sourceBtn.classList.remove('active');
    contentDiv.innerHTML = '';
    contentDiv.appendChild(iframe);
  }};

  sourceBtn.onclick = function() {{
    if (showingSource) return;
    showingSource = true;
    sourceBtn.classList.add('active');
    renderedBtn.classList.remove('active');
    contentDiv.innerHTML = '';
    contentDiv.appendChild(renderCodeLines(rawContent, 'html', 0, 0));
  }};

  toolbar.appendChild(renderedBtn);
  toolbar.appendChild(sourceBtn);
  wrap.appendChild(toolbar);
  wrap.appendChild(contentDiv);
  body.style.padding = '0';
  body.innerHTML = '';
  body.appendChild(wrap);
}}

function formatSrtTime(sec) {{
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  return (h ? h + ':' : '') + String(m).padStart(2,'0') + ':' + String(s).padStart(2,'0');
}}

function playSrt(srtPath, timeSec) {{
  if (_backendOnline) {{
    fetch(BACKEND_URL + '/api/play?srt=' + encodeURIComponent(srtPath) + '&time=' + timeSec)
      .then(r => r.json())
      .then(d => {{ if (d.error) showToast('⚠ ' + d.error); else showToast('▶ Launching VLC at ' + formatSrtTime(timeSec)); }})
      .catch(() => _playSrtFrontend(srtPath, timeSec));
  }} else {{
    _playSrtFrontend(srtPath, timeSec);
  }}
}}

function _playSrtFrontend(srtPath, timeSec) {{
  // Find matching video entry in the catalog (same stem, different extension)
  const srtStem = srtPath.split(/[\\\\/]/).pop().replace(/\\.[^.]+$/, '').toLowerCase();
  const videoExts = new Set(['mp4','mkv','avi','mov','webm','m4v','flv','wmv']);
  let videoSrc = null;
  document.querySelectorAll('.video-entry[data-src]').forEach(el => {{
    const src = el.dataset.src || '';
    const stem = src.split(/[\\\\/]/).pop().replace(/\\.[^.]+$/, '').toLowerCase();
    const ext = src.split('.').pop().toLowerCase();
    if (stem === srtStem && videoExts.has(ext)) videoSrc = src;
  }});
  if (!videoSrc) {{
    showToast('⚠ No matching video found in catalog');
    return;
  }}
  const title = srtStem + ' @ ' + formatSrtTime(timeSec);
  launchPiP(videoSrc, title);
  // Seek after a short delay to allow video to load
  setTimeout(() => {{
    if (_pipEl) {{
      const vid = _pipEl.querySelector('video');
      if (vid) {{ vid.currentTime = timeSec; vid.play(); }}
    }}
  }}, 800);
}}

function escHtml(s) {{ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }}
function escAttr(s) {{ return String(s||'').replace(/\\\\/g,'\\\\\\\\').replace(/'/g,"\\'"); }}
function escapeRegex(s) {{ return s.replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&'); }}
function getBackendFileIcon(t) {{
  const m = {{pdf:'📄',video:'🎬',audio:'🎵',image:'🖼️',js:'⚡',py:'🐍',java:'☕',
             json:'🔧',xml:'🔧',html:'🌐',docx:'📝',xlsx:'📈',pptx:'📊',txt:'📋',md:'📋',
             srt:'💬',vtt:'💬'}};
  return m[t] || '📎';
}}

// ─── VLC launch from catalog ──────────────────────────────
function launchVLCFromCatalog(path) {{
  fetch(BACKEND_URL + '/api/play-video?path=' + encodeURIComponent(path))
    .then(r => r.json())
    .then(d => {{ if (d.error) showToast('⚠ ' + d.error); else showToast('▶ Launching VLC...'); }})
    .catch(() => showToast('⚠ Backend not reachable'));
}}

// ─── Floating / PiP video player ─────────────────────────
let _pipEl = null;
function launchPiP(src, title) {{
  if (_pipEl) {{ _pipEl.remove(); _pipEl = null; }}
  const wrap = document.createElement('div');
  wrap.className = 'pip-container';
  const bar = document.createElement('div');
  bar.className = 'pip-titlebar';
  const lbl = document.createElement('span');
  lbl.textContent = title || src.split(/[\\/]/).pop();
  lbl.style.cssText = 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:260px';
  const cls = document.createElement('button');
  cls.className = 'pip-close';
  cls.textContent = '✕';
  cls.title = 'Close floating player';
  cls.onclick = () => {{ wrap.remove(); _pipEl = null; }};
  bar.appendChild(lbl);
  bar.appendChild(cls);
  const vid = document.createElement('video');
  vid.src = src;
  vid.controls = true;
  vid.autoplay = true;
  wrap.appendChild(bar);
  wrap.appendChild(vid);
  document.body.appendChild(wrap);
  _pipEl = wrap;
}}

// ─── Permalink / deep-link ────────────────────────────────
function copyPermalink(path) {{
  const base = location.href.replace(/[?#].*$/, '');
  const url = base + '?doc=' + encodeURIComponent(path || '');
  if (navigator.clipboard) {{
    navigator.clipboard.writeText(url).then(() => showToast('🔗 Permalink copied!'));
  }} else {{
    const ta = document.createElement('textarea');
    ta.value = url; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); document.body.removeChild(ta);
    showToast('🔗 Permalink copied!');
  }}
}}

function _handleDocParam() {{
  const doc = new URLSearchParams(location.search).get('doc');
  if (!doc) return;
  const normDoc = doc.replace(/\\\\/g, '/').toLowerCase();

  function normPath(p) {{ return (p || '').replace(/\\\\/g, '/').toLowerCase(); }}

  // Helper: only direct children of courseFiles that are cat-group or subdir-group
  function topLevelGroups(card) {{
    const cf = card.querySelector('.course-files');
    if (!cf) return [];
    return Array.from(cf.children).filter(el =>
      el.classList.contains('cat-group') || el.classList.contains('subdir-group'));
  }}

  function expandCard(card) {{
    card.style.display = '';
    const cardBody = card.querySelector('.course-body');
    if (cardBody) {{ cardBody.classList.remove('collapsed'); cardBody.style.maxHeight = 'none'; cardBody.style.opacity = '1'; }}
    card.querySelector('.course-chevron')?.classList.add('open');
  }}

  function expandSubdir(subdir) {{
    const sdBody = document.getElementById('sdbody-' + subdir.id);
    if (sdBody) {{ sdBody.classList.remove('collapsed'); sdBody.style.maxHeight = 'none'; sdBody.style.opacity = '1'; }}
    subdir.querySelector('.subdir-chevron')?.classList.add('open');
  }}

  // 1. File entry match
  let target = null;
  document.querySelectorAll('.file-entry[data-abs-path]').forEach(el => {{
    if (normPath(el.dataset.absPath) === normDoc) target = el;
  }});

  // 2. Subdir (folder) match
  let targetSubdir = null;
  if (!target) {{
    document.querySelectorAll('.subdir-group[data-abs-path]').forEach(el => {{
      if (normPath(el.dataset.absPath) === normDoc) targetSubdir = el;
    }});
  }}

  // 3. Course card (top-level folder) match
  let targetCard = null;
  if (!target && !targetSubdir) {{
    document.querySelectorAll('.course-card[data-abs-path]').forEach(el => {{
      if (normPath(el.dataset.absPath) === normDoc) targetCard = el;
    }});
  }}

  // Hide all course cards
  document.querySelectorAll('.course-card').forEach(c => c.style.display = 'none');

  if (targetCard) {{
    // Course permalink: just expand the whole course
    expandCard(targetCard);
    setTimeout(() => targetCard.scrollIntoView({{ block: 'start', behavior: 'smooth' }}), 300);
    return;
  }}

  const found = target || targetSubdir;
  if (!found) return;

  const card = found.closest('.course-card');
  if (!card) return;
  expandCard(card);

  // Hide only TOP-LEVEL groups (direct children of .course-files), not nested cat-groups
  const tlGroups = topLevelGroups(card);

  if (target) {{
    const parentSubdir = target.closest('.subdir-group');
    const parentCatGroup = target.closest('.cat-group');

    // Hide all top-level groups
    tlGroups.forEach(g => g.style.display = 'none');

    if (parentSubdir) {{
      parentSubdir.style.display = '';
      expandSubdir(parentSubdir);
    }}
    if (parentCatGroup) {{
      parentCatGroup.style.display = '';
      // Hide sibling file entries, show only the target
      parentCatGroup.querySelectorAll('.file-entry').forEach(el => {{
        el.style.display = el === target ? '' : 'none';
      }});
    }}

    setTimeout(() => target.scrollIntoView({{ block: 'center', behavior: 'smooth' }}), 300);

  }} else if (targetSubdir) {{
    // Folder permalink: hide other top-level groups, show this subdir fully expanded
    tlGroups.forEach(g => {{ g.style.display = g === targetSubdir ? '' : 'none'; }});
    expandSubdir(targetSubdir);
    // All cat-groups and file-entries inside the subdir remain visible (not touched)
    setTimeout(() => targetSubdir.scrollIntoView({{ block: 'start', behavior: 'smooth' }}), 300);
  }}
}}

// ─── SRT viewer with clickable timestamps ─────────────────
function _parseSRTTime(ts) {{
  const m = /(\\d{{2}}):(\\d{{2}}):(\\d{{2}}),(\\d{{3}})/.exec(ts);
  if (!m) return null;
  return parseInt(m[1]) * 3600 + parseInt(m[2]) * 60 + parseInt(m[3]) + parseInt(m[4]) / 1000;
}}

function _uriToFsPath(uri) {{
  // Normalize and decode
  const dec = decodeURIComponent(uri);
  // file:///C:/path  → C:/path  (local Windows drive)
  if (/^file:\\/\\/\\/[A-Za-z]:/.test(dec)) return dec.slice(8);
  // file:////server/share → //server/share  (standard UNC URI)
  if (dec.startsWith('file:////')) return '//' + dec.slice(9);
  // file://server/share  → //server/share  (Python Path.as_uri() UNC format)
  if (dec.startsWith('file://')) return '//' + dec.slice(7);
  return dec;
}}

function renderSRTViewer(content, srtFsPath) {{
  const SRT_TS_RE = /^(\\d{{2}}:\\d{{2}}:\\d{{2}},\\d{{3}})\\s*-->\\s*(\\d{{2}}:\\d{{2}}:\\d{{2}},\\d{{3}})/;
  const wrap = document.createElement('div');
  wrap.style.cssText = 'font-family:var(--font-mono);font-size:0.83rem;line-height:1.9;padding:8px';
  content.split('\\n').forEach(line => {{
    const row = document.createElement('div');
    const tsMatch = SRT_TS_RE.exec(line);
    if (tsMatch) {{
      const sec = _parseSRTTime(tsMatch[1]);
      const btn = document.createElement('button');
      btn.style.cssText = 'background:rgba(108,142,245,0.12);border:1px solid var(--accent);border-radius:4px;' +
        'color:var(--accent);cursor:pointer;font-family:inherit;font-size:inherit;padding:0 8px;text-align:left;width:100%';
      btn.title = 'Open in VLC at ' + tsMatch[1];
      btn.textContent = line;
      btn.onclick = () => playSrt(srtFsPath, sec);
      row.appendChild(btn);
    }} else {{
      row.style.padding = '0 4px';
      if (/^\\d+$/.test(line.trim())) {{
        row.style.cssText += ';color:var(--text3);font-size:0.75rem';
      }}
      row.textContent = line;
    }}
    wrap.appendChild(row);
  }});
  return wrap;
}}

// ─── Soundex for filename search ──────────────────────────────────────────────
const _SDXMAP_JS = {{a:0,b:1,c:2,d:3,e:0,f:1,g:2,h:0,i:0,j:2,k:2,l:4,m:5,n:5,o:0,p:1,q:2,r:6,s:2,t:3,u:0,v:1,w:0,x:2,y:0,z:2}};
function jsSoundex(word) {{
  if (!word) return '0000';
  word = word.toLowerCase();
  let result = word[0];
  let prev = _SDXMAP_JS[word[0]] ?? 0;
  for (let i = 1; i < word.length && result.length < 4; i++) {{
    const c = _SDXMAP_JS[word[i]];
    if (c !== undefined && c !== 0 && c !== prev) result += c;
    if (c !== undefined && c !== 0) prev = c;
  }}
  return result.padEnd(4, '0').slice(0, 4);
}}

function _normLang(text, lang) {{
  text = text.toLowerCase();
  if (lang === 'de') {{
    [['tsch','t'],['sch','s'],['ph','f'],['ch','x'],['ck','k'],['dt','t'],['th','t'],
     ['ae','a'],['oe','o'],['ue','u'],['ä','a'],['ö','o'],['ü','u'],
     ['ß','s'],['ss','s'],['v','f'],['w','v'],['y','i']]
      .forEach(([o,n]) => {{ text = text.split(o).join(n); }});
  }} else if (lang === 'en') {{
    [['ph','f'],['wh','w']].forEach(([o,n]) => {{ text = text.split(o).join(n); }});
  }} else if (lang === 'es') {{
    [['á','a'],['é','e'],['í','i'],['ó','o'],['ú','u'],['ü','u'],['ñ','n'],['ll','y'],['rr','r']]
      .forEach(([o,n]) => {{ text = text.split(o).join(n); }});
  }}
  return text;
}}

function soundexFileMatch(query, filename) {{
  const lang = document.getElementById('searchLang')?.value || '';
  const normQ = _normLang(query, lang);
  const normF = _normLang(filename, lang);
  const qWords = normQ.split(/\\W+/).filter(w => w.length >= 2);
  const fWords = normF.split(/\\W+/).filter(w => w.length >= 2);
  return qWords.some(qw => fWords.some(fw => jsSoundex(qw) === jsSoundex(fw)));
}}

// ─── Theme ───────────────────────────────────────────────
const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
let isDark = localStorage.getItem('theme') !== 'light';
function applyTheme() {{
  document.body.classList.toggle('light', !isDark);
  document.querySelector('.theme-toggle').textContent = isDark ? '🌙' : '☀️';
}}
function toggleTheme() {{
  isDark = !isDark;
  localStorage.setItem('theme', isDark ? 'dark' : 'light');
  applyTheme();
}}
applyTheme();

// ─── Course Toggle ────────────────────────────────────────
function toggleCourse(idx) {{
  const body = document.getElementById('body-' + idx);
  const chev = document.getElementById('chevron-' + idx);
  const isOpen = !body.classList.contains('collapsed');
  if (isOpen) {{
    body.classList.add('collapsed');
    chev.classList.remove('open');
    // Set max-height explicitly before collapsing
    body.style.maxHeight = body.scrollHeight + 'px';
    requestAnimationFrame(() => {{ body.style.maxHeight = '0'; body.style.opacity = '0'; }});
  }} else {{
    body.classList.remove('collapsed');
    chev.classList.add('open');
    body.style.maxHeight = body.scrollHeight + 'px';
    body.style.opacity = '1';
    setTimeout(() => {{ body.style.maxHeight = 'none'; }}, 400);
    loadNote(idx);
    loadCourseDurations(idx);
  }}
}}

function expandAll() {{
  document.querySelectorAll('.course-card:not(.search-hidden)').forEach((card, i) => {{
    const idx = card.id.replace('course-', '');
    const body = document.getElementById('body-' + idx);
    const chev = document.getElementById('chevron-' + idx);
    if (body.classList.contains('collapsed')) {{
      body.classList.remove('collapsed');
      chev.classList.add('open');
      body.style.maxHeight = 'none';
      body.style.opacity = '1';
      loadNote(parseInt(idx));
      loadCourseDurations(parseInt(idx));
    }}
  }});
}}

function collapseAll() {{
  document.querySelectorAll('.course-card').forEach(card => {{
    const idx = card.id.replace('course-', '');
    const body = document.getElementById('body-' + idx);
    const chev = document.getElementById('chevron-' + idx);
    body.classList.add('collapsed');
    chev.classList.remove('open');
    body.style.maxHeight = '0';
    body.style.opacity = '0';
  }});
}}

// ─── Lazy-load media durations ────────────────────────────
function loadCourseDurations(idx) {{
  const card = document.getElementById('course-' + idx);
  if (!card) return;
  card.querySelectorAll('.file-entry[data-src]').forEach(entry => {{
    const src = entry.dataset.src;
    if (!src) return;
    const hint = entry.querySelector('.duration-hint');
    if (!hint || hint.dataset.duration) return; // already loaded
    const isVideo = entry.classList.contains('video-entry');
    const tmpMedia = document.createElement(isVideo ? 'video' : 'audio');
    tmpMedia.preload = 'metadata';
    tmpMedia.src = src;
    tmpMedia.addEventListener('loadedmetadata', () => {{
      const d = tmpMedia.duration;
      if (isFinite(d)) {{
        const m = Math.floor(d / 60);
        const s = Math.floor(d % 60).toString().padStart(2, '0');
        hint.textContent = '⏱ ' + m + ':' + s;
        hint.dataset.duration = '1';
      }}
      tmpMedia.src = '';
    }});
  }});
}}

// ─── Subdir Toggle ────────────────────────────────────────
function toggleSubdir(id) {{
  const body = document.getElementById('sdbody-' + id);
  const header = body.previousElementSibling;
  const chev = header.querySelector('.subdir-chevron');
  if (!body) return;
  const isOpen = !body.classList.contains('collapsed');
  if (isOpen) {{
    body.classList.add('collapsed');
    chev.classList.remove('open');
    body.style.maxHeight = '0';
    body.style.opacity = '0';
  }} else {{
    body.classList.remove('collapsed');
    chev.classList.add('open');
    body.style.maxHeight = body.scrollHeight + 'px';
    body.style.opacity = '1';
    setTimeout(() => {{ body.style.maxHeight = 'none'; }}, 400);
  }}
}}

// ─── File Preview Toggle ──────────────────────────────────
function togglePreview(header) {{
  const preview = header.nextElementSibling;
  if (!preview || !preview.classList.contains('file-preview')) return;
  const isOpen = !preview.classList.contains('collapsed');
  if (isOpen) {{
    // Collapsing: pause media, unload YouTube, hide completely
    const media = preview.querySelector('video, audio');
    if (media) {{ media.pause(); media.currentTime = 0; }}
    const ytid = preview.dataset.ytid;
    if (ytid) {{
      const iframe = preview.querySelector('iframe');
      if (iframe) iframe.src = '';
    }}
    preview.classList.add('collapsed');
    preview.style.display = 'none';
  }} else {{
    // Expanding: show, then load/play
    preview.classList.remove('collapsed');
    preview.style.display = 'block';
    const ytid = preview.dataset.ytid;
    if (ytid) {{
      const iframe = preview.querySelector('iframe');
      if (iframe) iframe.src = 'https://www.youtube.com/embed/' + ytid + '?autoplay=1&rel=0';
    }}
    const media = preview.querySelector('video, audio');
    if (media) {{
      media.load();
      media.play().catch(() => {{}});
    }}
  }}
}}

// ─── PDF Viewer ───────────────────────────────────────────
function openPDF(el) {{
  const path = el.dataset.filePath;
  const name = el.dataset.fileName || path.split('/').pop();
  setModalTitle(name, path);
  document.getElementById('modalBody').innerHTML =
    '<iframe src="' + path + '" title="PDF Preview"></iframe>';
  document.getElementById('modal').classList.remove('maximized');
  document.getElementById('modalOverlay').classList.add('open');
}}

// ─── Image Modal ──────────────────────────────────────────
function openImageModal(el) {{
  const path = el.dataset.filePath;
  const name = el.dataset.fileName || path.split('/').pop();
  setModalTitle(name, path);
  document.getElementById('modalBody').innerHTML =
    '<img src="' + path + '" class="modal-image-preview" alt="' + name.replace(/"/g,'&quot;') + '">';
  document.getElementById('modal').classList.remove('maximized');
  document.getElementById('modalOverlay').classList.add('open');
}}

// ─── Text File Viewer ─────────────────────────────────────
function openTextFile(el) {{
  const path = el.dataset.filePath;   // file:// URI
  const name = el.dataset.fileName || path.split('/').pop();
  const ext = name.split('.').pop().toLowerCase();
  const fsPath = _uriToFsPath(path);  // filesystem path for backend API
  setModalTitle(name, path);
  document.getElementById('modalBody').innerHTML = '<pre>Loading...</pre>';
  document.getElementById('modal').classList.remove('maximized');
  document.getElementById('modalOverlay').classList.add('open');

  function _render(content) {{
    const body = document.getElementById('modalBody');
    if (ext === 'srt' || ext === 'vtt') {{
      body.innerHTML = '';
      body.appendChild(renderSRTViewer(content, fsPath));
    }} else {{
      body.innerHTML = '<pre>' + content.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</pre>';
    }}
  }}

  // Prefer backend (avoids browser CORS/security block on file:// and UNC URIs)
  if (_backendOnline) {{
    fetch(BACKEND_URL + '/api/file-text?path=' + encodeURIComponent(fsPath))
      .then(r => r.json())
      .then(d => _render(d.content || ''))
      .catch(() => {{
        const body = document.getElementById('modalBody');
        if (body) body.innerHTML = '<div style="padding:16px;line-height:1.7">' +
          '<span style="color:var(--warning)">⚠ Cannot load file content.</span><br>' +
          '<small style="color:var(--text3)">Start the backend server for network/UNC file access.</small><br><br>' +
          '<a href="' + escHtml(path) + '" target="_blank" style="color:var(--accent)">Open in new tab ↗</a>' +
          '</div>';
      }});
    return;
  }}

  // Fallback: direct fetch (works only for simple local file:// pages)
  fetch(path)
    .then(r => r.text())
    .then(text => _render(text))
    .catch(() => {{
      const body = document.getElementById('modalBody');
      if (body) body.innerHTML = '<div style="padding:16px;line-height:1.7">' +
        '<span style="color:var(--warning)">⚠ Cannot load file content.</span><br>' +
        '<small style="color:var(--text3)">Start the backend server for network/UNC file access.</small><br><br>' +
        '<a href="' + escHtml(path) + '" target="_blank" style="color:var(--accent)">Open in new tab ↗</a>' +
        '</div>';
    }});
}}

function closeModal(e) {{
  if (e.target === document.getElementById('modalOverlay')) closeModalDirect();
}}
function closeModalDirect() {{
  document.getElementById('modalOverlay').classList.remove('open');
  document.getElementById('modal').classList.remove('maximized');
  const mb = document.getElementById('modalBody');
  mb.innerHTML = '';
  mb.style.padding = '';
}}
document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeModalDirect(); }});

// ─── Modal Maximize ───────────────────────────────────────
function toggleModalMaximize() {{
  document.getElementById('modal').classList.toggle('maximized');
}}

// ─── Update group visibility after search/filter ──────────
function updateGroupVisibility(scope) {{
  const cards = scope ? [scope] : document.querySelectorAll('.course-card');
  cards.forEach(card => {{
    // Cat groups
    card.querySelectorAll('.cat-group').forEach(group => {{
      const anyVisible = Array.from(group.querySelectorAll('.file-entry'))
        .some(fe => !fe.classList.contains('file-search-hidden'));
      group.classList.toggle('group-hidden', !anyVisible);
    }});
    // Subdir groups
    card.querySelectorAll('.subdir-group').forEach(group => {{
      const anyVisible = Array.from(group.querySelectorAll('.file-entry'))
        .some(fe => !fe.classList.contains('file-search-hidden'));
      group.classList.toggle('group-hidden', !anyVisible);
    }});
  }});
}}

// ─── Search ───────────────────────────────────────────────
let currentFilter = 'all';
function handleSearch(query) {{
  query = query.toLowerCase().trim();
  let visible = 0;

  document.querySelectorAll('.course-card').forEach(card => {{
    const courseName = card.dataset.name || '';
    const keywords = card.dataset.keywords || '';

    if (!query) {{
      card.classList.remove('search-hidden', 'search-match');
      // Show all files
      card.querySelectorAll('.file-entry').forEach(f => {{
        f.classList.remove('file-search-hidden');
        if (currentFilter !== 'all') {{
          f.classList.toggle('file-search-hidden', f.dataset.cat !== currentFilter);
        }}
      }});
      // Restore all groups
      card.querySelectorAll('.cat-group, .subdir-group').forEach(g => g.classList.remove('group-hidden'));
      visible++;
      return;
    }}

    const useSoundexFiles = document.getElementById('searchSoundexFiles')?.checked;
    const courseMatch = courseName.includes(query) || (useSoundexFiles && soundexFileMatch(query, courseName));
    const fileMatches = [];
    card.querySelectorAll('.file-entry').forEach(f => {{
      const fname = f.dataset.name || '';
      const match = fname.includes(query) || (useSoundexFiles && soundexFileMatch(query, fname));
      f.classList.toggle('file-search-hidden', !match);
      if (match) fileMatches.push(f);
    }});

    updateGroupVisibility(card);

    // Auto-expand subdir groups that have matches
    card.querySelectorAll('.subdir-group').forEach(sg => {{
      if (!sg.classList.contains('group-hidden')) {{
        const body = sg.querySelector('.subdir-body');
        const chev = sg.querySelector('.subdir-chevron');
        if (body && body.classList.contains('collapsed')) {{
          body.classList.remove('collapsed');
          if (chev) chev.classList.add('open');
          body.style.maxHeight = 'none';
          body.style.opacity = '1';
        }}
      }}
    }});

    if (courseMatch || fileMatches.length > 0) {{
      card.classList.remove('search-hidden');
      card.classList.add('search-match');
      // Auto-expand if file matches
      if (fileMatches.length > 0) {{
        const idx = card.id.replace('course-', '');
        const body = document.getElementById('body-' + idx);
        if (body.classList.contains('collapsed')) {{
          body.classList.remove('collapsed');
          document.getElementById('chevron-' + idx).classList.add('open');
          body.style.maxHeight = 'none';
          body.style.opacity = '1';
        }}
      }}
      visible++;
    }} else {{
      card.classList.add('search-hidden');
      card.classList.remove('search-match');
    }}
  }});

  const countEl = document.getElementById('searchCount');
  const noResults = document.getElementById('noResults');
  if (query) {{
    countEl.style.display = 'block';
    countEl.textContent = visible + ' course(s) matched';
    noResults.style.display = visible === 0 ? 'block' : 'none';
  }} else {{
    countEl.style.display = 'none';
    noResults.style.display = 'none';
    closeBackendResults();
  }}

  // Backend search (debounced)
  clearTimeout(backendSearchTimer);
  if (query && query.length >= 2) {{
    backendSearchTimer = setTimeout(() => backendSearch(query), 3000);
  }} else {{
    closeBackendResults();
  }}
}}

// ─── Category Filter ──────────────────────────────────────
function filterCat(cat, btn) {{
  currentFilter = cat;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');

  document.querySelectorAll('.file-entry').forEach(f => {{
    if (cat === 'all') {{
      f.classList.remove('file-search-hidden');
    }} else {{
      f.classList.toggle('file-search-hidden', f.dataset.cat !== cat);
    }}
  }});

  // Update group visibility after filter
  updateGroupVisibility(null);

  // Hide/show entire course cards based on whether they have any matching files
  document.querySelectorAll('.course-card').forEach(card => {{
    if (cat === 'all') {{
      card.classList.remove('filter-type-hidden');
    }} else {{
      const hasVisible = Array.from(card.querySelectorAll('.file-entry'))
        .some(f => !f.classList.contains('file-search-hidden'));
      card.classList.toggle('filter-type-hidden', !hasVisible);
    }}
  }});
}}

// ─── Notes ───────────────────────────────────────────────
function saveNote(idx) {{
  const val = document.getElementById('notes-' + idx).value;
  const key = 'course_note_' + idx;
  if (val.trim()) {{
    localStorage.setItem(key, val);
    showToast('✅ Note saved!');
  }} else {{
    localStorage.removeItem(key);
  }}
  updateNotesCount();
}}

function loadNote(idx) {{
  const val = localStorage.getItem('course_note_' + idx);
  if (val) document.getElementById('notes-' + idx).value = val;
}}

function updateNotesCount() {{
  const keys = Object.keys(localStorage).filter(k => k.startsWith('course_note_'));
  const el = document.getElementById('notesCount');
  el.textContent = keys.length > 0 ? keys.length + ' note(s) saved.' : 'No notes saved yet.';
}}

function toggleNotesPanel() {{
  const popup = document.getElementById('notesPanelPopup');
  popup.classList.toggle('open');
  updateNotesCount();
}}

function exportNotes() {{
  const courseNames = {json.dumps([c["name"] for c in courses])};
  let content = 'COURSE CATALOG NOTES\\nExported: ' + new Date().toLocaleString() + '\\n';
  content += '='.repeat(60) + '\\n\\n';
  const keys = Object.keys(localStorage).filter(k => k.startsWith('course_note_')).sort();
  if (keys.length === 0) {{ showToast('No notes to export'); return; }}
  keys.forEach(k => {{
    const idx = parseInt(k.replace('course_note_', ''));
    const courseName = courseNames[idx] || ('Course ' + idx);
    content += '📚 ' + courseName + '\\n';
    content += '-'.repeat(40) + '\\n';
    content += localStorage.getItem(k) + '\\n\\n';
  }});
  const blob = new Blob([content], {{type: 'text/plain'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'course_notes.txt';
  a.click();
  showToast('📥 Notes exported!');
}}

function clearAllNotes() {{
  if (!confirm('Clear ALL saved notes? This cannot be undone.')) return;
  Object.keys(localStorage).filter(k => k.startsWith('course_note_')).forEach(k => localStorage.removeItem(k));
  document.querySelectorAll('.notes-textarea').forEach(t => t.value = '');
  updateNotesCount();
  showToast('🗑 All notes cleared');
}}

// ─── Toast ────────────────────────────────────────────────
function showToast(msg) {{
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2500);
}}

// ─── Init ─────────────────────────────────────────────────
updateNotesCount();
</script>
</body>
</html>'''

    html_path = Path(output_dir) / "course_catalog.html"
    html_path.write_text(html, encoding="utf-8")
    return str(html_path)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def _ask_docker_start(script_dir: str, courses_dir: str):
    """Ask user if they want to start Elasticsearch via Docker for enhanced search.
    Works cross-platform: Windows, macOS, Linux/Ubuntu.
    """
    import shutil
    import subprocess
    import urllib.request

    compose_file = Path(script_dir) / "docker-compose.yml"
    if not compose_file.exists():
        return

    if not shutil.which("docker"):
        return  # Docker not installed

    print()
    print("─" * 60)
    print("  Elasticsearch — Enhanced Search (optional)")
    print("─" * 60)
    print("  Start Elasticsearch via Docker for full-text search inside")
    print("  PDFs, subtitles (with timestamps), documents, and more?")
    print()
    try:
        answer = input("  Start Docker Elasticsearch? [y/N]: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return

    if answer not in ('y', 'yes'):
        print()
        return

    # Determine compose command (Docker Compose v2 vs v1)
    compose_args = None
    try:
        subprocess.run(["docker", "compose", "version"],
                       capture_output=True, check=True, timeout=10)
        compose_args = ["docker", "compose"]
    except Exception:
        if shutil.which("docker-compose"):
            compose_args = ["docker-compose"]

    if compose_args is None:
        print("  Warning: Docker Compose not found. Skipping.", file=sys.stderr)
        print()
        return

    cwd = str(Path(script_dir))
    env = os.environ.copy()
    env["COURSES_PATH"] = str(Path(courses_dir).resolve())

    print()
    print("  Starting Elasticsearch container...")
    try:
        result = subprocess.run(
            compose_args + ["-f", str(compose_file), "up", "-d", "elasticsearch"],
            cwd=cwd, env=env, timeout=120,
        )
        if result.returncode != 0:
            print("  Warning: Failed to start Elasticsearch. Check docker logs.", file=sys.stderr)
            print()
            return
    except subprocess.TimeoutExpired:
        print("  Warning: Timeout starting Elasticsearch.", file=sys.stderr)
        print()
        return
    except Exception as e:
        print(f"  Warning: Docker error: {e}", file=sys.stderr)
        print()
        return

    # Wait for ES health
    import time
    print("  Waiting for Elasticsearch to be ready (up to 90s)...", end='', flush=True)
    es_ready = False
    for attempt in range(30):
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:9200/_cluster/health", timeout=3
            ) as resp:
                data = json.loads(resp.read())
                if data.get("status") in ("green", "yellow"):
                    es_ready = True
                    break
        except Exception:
            pass
        print(".", end='', flush=True)
        time.sleep(3)
    print()

    if not es_ready:
        print("  Warning: Elasticsearch did not become ready. Continuing without it.")
        print()
        return

    print("  Elasticsearch is ready!")
    print()

    # Ask about indexing
    try:
        idx_answer = input("  Index course files for search now? [Y/n]: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return

    if idx_answer in ('n', 'no'):
        print()
        return

    print("  Running indexer (incremental — only new/changed files)...")
    try:
        result = subprocess.run(
            compose_args + ["-f", str(compose_file), "run", "--rm", "indexer"],
            cwd=cwd, env=env, timeout=3600,
        )
        if result.returncode == 0:
            print("  Indexing complete!")
        else:
            print("  Warning: Indexer finished with errors. Check output above.")
    except subprocess.TimeoutExpired:
        print("  Warning: Indexer timed out (1h). Large collections may need more time.")
    except Exception as e:
        print(f"  Warning: Indexer error: {e}", file=sys.stderr)

    print()


def _update_env_search_path(script_dir: str, new_path: str) -> None:
    """If env.py exists in script_dir, APPEND new_path to SEARCH_BASE_PATH
    (semicolon-separated) if not already present.  Does NOT replace existing paths."""
    env_file = Path(script_dir) / "env.py"
    if not env_file.exists():
        return
    try:
        content = env_file.read_text(encoding="utf-8")
    except OSError:
        return

    import re as _re
    # Normalize paths for comparison (strip trailing sep, lower on Windows)
    def _norm(p):
        return str(Path(p).resolve()).rstrip("\\/").lower()

    # Find active (non-commented) SEARCH_BASE_PATH line
    pattern = _re.compile(r'^(SEARCH_BASE_PATH\s*=\s*r?["\'])(.+?)(["\'])\s*$', _re.MULTILINE)
    match = pattern.search(content)
    if not match:
        return  # not found — leave file untouched

    prefix, current_value, quote = match.group(1), match.group(2), match.group(3)

    # Parse existing paths (semicolon-separated)
    existing_paths = [p.strip() for p in current_value.split(';') if p.strip()]
    existing_norms = {_norm(p) for p in existing_paths}

    new_norm = _norm(new_path)
    if new_norm in existing_norms:
        return  # already present — nothing to do

    # Append the new path
    existing_paths.append(str(Path(new_path).resolve()))
    new_val = ';'.join(existing_paths)
    new_line = f'{prefix}{new_val}{quote}'

    updated = content[:match.start()] + new_line + content[match.end():]
    try:
        env_file.write_text(updated, encoding="utf-8")
        print(f"📝 env.py: SEARCH_BASE_PATH += {new_path}")
    except OSError as e:
        print(f"⚠️  Could not update env.py: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Generate a Course Catalog (Markdown + HTML) from a directory of courses.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python catalog_generator.py /path/to/courses
  python catalog_generator.py /path/to/courses --depth 3
  python catalog_generator.py C:\\\\Users\\\\Me\\\\Courses --depth 2 --output C:\\\\Users\\\\Me\\\\Desktop
        """
    )
    parser.add_argument("courses_dir",
                        help="Path to the directory containing course folders")
    parser.add_argument("--depth", type=int, default=2,
                        help="How many subdirectory levels to scan inside each course (default: 2)")
    parser.add_argument("--output", default=".",
                        help="Output directory for generated files (default: current directory)")
    parser.add_argument("--force-youtube", action="store_true",
                        help="Embed YouTube player for videos whose filename contains a YouTube ID "
                             "(e.g. 'Lesson 01 [dQw4w9WgXcQ].mp4'). By default the local video file is played.")
    args = parser.parse_args()

    courses_dir = args.courses_dir
    depth = max(1, min(args.depth, 6))
    output_dir = args.output
    force_youtube = args.force_youtube

    # Create output dir if needed
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Auto-update SEARCH_BASE_PATH in env.py if the path has changed
    script_dir = Path(__file__).parent
    _update_env_search_path(str(script_dir), courses_dir)

    # Optionally start Docker Elasticsearch for enhanced search
    _ask_docker_start(str(script_dir), courses_dir)

    print(f"📂 Scanning: {courses_dir}")
    print(f"🔍 Depth:    {depth} levels")
    print(f"📤 Output:   {output_dir}")
    print(f"▶  YouTube:  {'forced (--force-youtube)' if force_youtube else 'disabled (local video by default)'}")
    print()

    courses = scan_directory(courses_dir, max_depth=depth)

    if not courses:
        print("⚠️  No course directories found!", file=sys.stderr)
        sys.exit(1)

    print(f"✅ Found {len(courses)} courses:")
    for c in courses:
        print(f"   🎓 {c['name']} — {c['file_count']} files ({human_size(c['total_size'])})")
    print()

    print("📝 Generating Markdown...")
    md_path = generate_markdown(courses, output_dir, courses_dir)
    print(f"   → {md_path}")

    print("🌐 Generating HTML...")
    html_path = generate_html(courses, output_dir, force_youtube)
    print(f"   → {html_path}")

    print()
    print("✨ Done! Open the HTML file in your browser to explore your courses.")


if __name__ == "__main__":
    main()
