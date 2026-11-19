"""
env.py — Project-specific configuration for validate.py.

Copy this file (or env.example.py) to the tools/ directory of any project
and adjust the paths to match your structure.
"""

from pathlib import Path

# ── Project identity ──────────────────────────────────────────────────────────
# Root of the project (by default: parent of tools/)
ROOT = Path(__file__).parent.parent.resolve()

PROJECT_NAME = 'Course Catalog Generator'

# ── Python files ──────────────────────────────────────────────────────────────
# Directories to scan recursively for *.py files (relative to ROOT)
PYTHON_DIRS = ['backend', 'deploy']

# Individual files to add beyond directory scanning (relative to ROOT)
PYTHON_EXTRA: list[str] = []

# Subdirectory names to skip while scanning
PYTHON_EXCLUDE_DIRS = ['__pycache__', '.venv', 'venv', 'node_modules', '.git', 'dist', 'build']

# ── JavaScript files ──────────────────────────────────────────────────────────
JS_DIRS = ['frontend/assets']
JS_EXTRA: list[str] = []
JS_EXCLUDE_DIRS = ['node_modules', '.venv', 'dist']

# ── CSS files ─────────────────────────────────────────────────────────────────
CSS_DIRS = ['frontend/assets']
CSS_EXTRA: list[str] = []
CSS_EXCLUDE_DIRS = ['node_modules', 'dist']

# ── HTML files ────────────────────────────────────────────────────────────────
# Explicit list (relative to ROOT); takes priority over HTML_DIRS
HTML_FILES = [
    'index.html',
    'frontend/search-console.html',
    'frontend/chat.html',
]

# Directories to scan for *.html files (alternative to explicit list)
HTML_DIRS: list[str] = []
HTML_EXTRA: list[str] = []
HTML_EXCLUDE_DIRS = ['node_modules', 'dist', '.venv']

# ── Lint tool configs ─────────────────────────────────────────────────────────
# Paths relative to this file's directory (tools/) or absolute.
ESLINT_CONFIG       = 'eslint.config.mjs'
HTMLVALIDATE_CONFIG = '.htmlvalidate.json'
STYLELINT_CONFIG    = '.stylelintrc.json'

# ── Report output ─────────────────────────────────────────────────────────────
# Set REPORT_FILE to save a report automatically after every run.
# Paths relative to ROOT, or absolute.  None = only print to console.
REPORT_FILE: str | None = None        # e.g. 'tools/validation_report.html'
REPORT_FORMAT = 'html'                # 'html' | 'txt'
