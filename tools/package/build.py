#!/usr/bin/env python3
"""
Catalog — DMS Lite  |  Native App Builder
==========================================
Packages the application as a native installable app for macOS, Windows,
or Linux using PyInstaller + pywebview.

Requirements:
    pip install pyinstaller pywebview

Usage:
    python3 tools/package/build.py              # auto-detect platform
    python3 tools/package/build.py --platform mac
    python3 tools/package/build.py --platform win
    python3 tools/package/build.py --platform linux

Output:
    dist/                           # platform-specific output
      macOS  → Catalog DMS Lite.app  (drag to /Applications)
      Windows→ Catalog DMS Lite.exe  (or use installer.iss for NSIS/Inno Setup)
      Linux  → catalog-dms-lite      (AppImage via appimagetool)
"""
import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent.resolve()
DIST = ROOT / "dist"
NAME = "Catalog DMS Lite"
ICON_PNG = ROOT / "tools" / "package" / "icon.png"
ICON_ICO = ROOT / "tools" / "package" / "icon.ico"
ICON_ICNS= ROOT / "tools" / "package" / "icon.icns"


def _run(cmd: list[str], **kw) -> None:
    print("$", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True, **kw)


def build_pyinstaller(extra_args: list[str]) -> None:
    """Run PyInstaller with common options."""
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onedir",          # bundle folder (faster start than --onefile)
        "--windowed",        # no console window
        "--name", NAME,
        "--distpath", str(DIST),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        # Data files to bundle
        "--add-data", f"{ROOT / 'index.html'}{_sep()}.",
        "--add-data", f"{ROOT / 'tools' / 'launcher.py'}{_sep()}tools",
        "--add-data", f"{ROOT / 'backend'}{_sep()}backend",
        "--add-data", f"{ROOT / 'frontend'}{_sep()}frontend",
        # Hidden imports for webview
        "--hidden-import", "webview",
        "--hidden-import", "webview.platforms",
    ] + extra_args + [str(ROOT / "tools" / "desktop.py")]

    _run(cmd)


def _sep() -> str:
    return ";" if platform.system() == "Windows" else ":"


def build_mac() -> None:
    print("\n── Building macOS .app ──────────────────────────────────────────")
    extra: list[str] = []
    if ICON_ICNS.exists():
        extra += ["--icon", str(ICON_ICNS)]
    build_pyinstaller(extra)
    app = DIST / f"{NAME}.app"
    if app.exists():
        print(f"\n✓ macOS app: {app}")
        print("  → Drag to /Applications to install")
        print("  → Or create a DMG: brew install create-dmg && create-dmg ...")
    else:
        # PyInstaller --onedir on macOS puts in a subfolder
        folder = DIST / NAME
        if folder.exists():
            print(f"✓ macOS bundle folder: {folder}/")
    _print_macos_notes()


def _print_macos_notes() -> None:
    print("""
macOS Notes:
  • First launch may require: System Settings → Privacy → Allow app
  • To sign for distribution: codesign --deep --sign "Developer ID" "dist/Catalog DMS Lite.app"
  • Create DMG with hdiutil or create-dmg
""")


def build_win() -> None:
    print("\n── Building Windows .exe ────────────────────────────────────────")
    extra: list[str] = []
    if ICON_ICO.exists():
        extra += ["--icon", str(ICON_ICO)]
    build_pyinstaller(extra)
    exe = DIST / NAME / f"{NAME}.exe"
    if exe.exists():
        print(f"\n✓ Windows executable: {exe}")
    _print_win_notes()


def _print_win_notes() -> None:
    iss = ROOT / "tools" / "package" / "installer.iss"
    print(f"""
Windows Notes:
  • Inno Setup installer script: {iss}
    Download Inno Setup: https://jrsoftware.org/isinfo.php
    Then: ISCC tools/package/installer.iss

  • To sign: signtool sign /fd SHA256 /a "dist\\Catalog DMS Lite\\Catalog DMS Lite.exe"

  • Microsoft App Installer (.msix) is also possible via msix-packaging-tool
""")


def build_linux() -> None:
    print("\n── Building Linux binary ────────────────────────────────────────")
    extra: list[str] = []
    if ICON_PNG.exists():
        extra += ["--icon", str(ICON_PNG)]
    build_pyinstaller(extra)
    binary = DIST / NAME / NAME
    if binary.exists():
        print(f"\n✓ Linux binary: {binary}")
    _print_linux_notes()


def _print_linux_notes() -> None:
    print("""
Linux Notes:
  • Run directly: ./dist/Catalog\\ DMS\\ Lite/"Catalog DMS Lite"
  • AppImage (portable, no install needed):
      Download appimagetool: https://appimage.github.io/appimagetool/
      Then: appimagetool dist/Catalog\\ DMS\\ Lite catalog-dms-lite.AppImage
  • .deb package: use fpm or dh-make to wrap the dist folder
  • Create a .desktop launcher for GNOME/KDE:
      [Desktop Entry]
      Name=Catalog DMS Lite
      Exec=/opt/catalog-dms-lite/Catalog\\ DMS\\ Lite
      Icon=/opt/catalog-dms-lite/icon.png
      Type=Application
      Categories=Office;
""")


def main() -> None:
    parser = argparse.ArgumentParser(description="Catalog DMS Lite — Build native app")
    parser.add_argument("--platform", choices=["mac", "win", "linux"],
                        default=None, help="Target platform (default: auto-detect)")
    args = parser.parse_args()

    # Check PyInstaller
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller not installed. Run: pip install pyinstaller", file=sys.stderr)
        sys.exit(1)

    # Check pywebview
    try:
        import webview  # noqa: F401
    except ImportError:
        print("pywebview not installed. Run: pip install pywebview", file=sys.stderr)
        sys.exit(1)

    plat = args.platform or {
        "Darwin": "mac", "Windows": "win", "Linux": "linux"
    }.get(platform.system(), "linux")

    DIST.mkdir(parents=True, exist_ok=True)

    if plat == "mac":
        build_mac()
    elif plat == "win":
        build_win()
    else:
        build_linux()

    print("\n✓ Build complete.")


if __name__ == "__main__":
    main()
