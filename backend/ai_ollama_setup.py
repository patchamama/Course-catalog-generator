#!/usr/bin/env python3
"""
ai_ollama_setup.py — Hardware-aware Ollama setup helper.

Detects GPU/CPU capabilities, recommends appropriate models,
checks Ollama installation, and guides through model download.

Usage:
    python backend/ai_ollama_setup.py                  # interactive setup
    python backend/ai_ollama_setup.py --detect         # print hardware profile only
    python backend/ai_ollama_setup.py --install-model  # install recommended model non-interactively
    python backend/ai_ollama_setup.py --json           # output JSON profile
"""

import os
import sys
import json
import shutil
import platform
import subprocess
import argparse
import urllib.request
import urllib.error
from typing import Optional

OLLAMA_URL = os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434')

# ── Model catalogue ────────────────────────────────────────────────────────────
# Each entry: (ollama_tag, size_gb, min_vram_gb, min_ram_gb, description)
MODELS = [
    # LLM — general purpose / RAG
    ("phi3.5",           2.2,  0,   6,  "Microsoft Phi-3.5 Mini — fast, good for docs, CPU-friendly"),
    ("llama3.2:3b",      2.0,  0,   6,  "Meta Llama 3.2 3B — solid multilingual, CPU-friendly"),
    ("qwen2.5:3b",       2.0,  0,   6,  "Alibaba Qwen 2.5 3B — excellent multilingual (EN/ES/DE/ZH)"),
    ("llama3.2:7b",      4.7,  6,   8,  "Meta Llama 3.2 7B — better quality, needs ~6 GB VRAM"),
    ("qwen2.5:7b",       4.7,  6,   8,  "Alibaba Qwen 2.5 7B — top multilingual quality at 7B"),
    ("qwen2.5:14b",      9.0,  12,  16, "Alibaba Qwen 2.5 14B — high quality, M1 Max / 12GB+ VRAM"),
    ("llama3.1:8b",      4.9,  6,   8,  "Meta Llama 3.1 8B — strong reasoning + tool use"),
    ("llama3.1:70b",    40.0,  40,  64, "Meta Llama 3.1 70B — near-GPT-4 quality, needs big GPU"),
    # Translation
    ("translategemma:4b",  3.3, 0,   6, "Google TranslateGemma 4B — multilingual translation (EN/ES/DE)"),
    ("translategemma:12b", 8.1, 8,  12, "Google TranslateGemma 12B — higher translation quality"),
]

# ── Hardware detection ─────────────────────────────────────────────────────────

def detect_hardware() -> dict:
    profile = {
        "platform":       sys.platform,
        "arch":           platform.machine(),
        "os_name":        platform.system(),
        "os_release":     platform.release(),
        "cpu_cores":      os.cpu_count() or 1,
        "ram_gb":         0.0,
        "gpu_type":       None,   # "apple_silicon" | "nvidia" | "amd" | None
        "gpu_vram_gb":    0.0,
        "gpu_name":       None,
        "ollama_installed": shutil.which("ollama") is not None,
        "is_wsl":         False,
    }

    # WSL detection
    try:
        with open("/proc/version") as f:
            if "microsoft" in f.read().lower():
                profile["is_wsl"] = True
    except Exception:
        pass

    # RAM
    try:
        import psutil
        profile["ram_gb"] = round(psutil.virtual_memory().total / 1e9, 1)
    except ImportError:
        # Fallback
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        kb = int(line.split()[1])
                        profile["ram_gb"] = round(kb / 1e6, 1)
                        break
        except Exception:
            pass
        if not profile["ram_gb"]:
            try:
                out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)
                profile["ram_gb"] = round(int(out.strip()) / 1e9, 1)
            except Exception:
                pass

    # Apple Silicon
    if profile["arch"] in ("arm64", "aarch64") and profile["os_name"] == "Darwin":
        profile["gpu_type"] = "apple_silicon"
        profile["gpu_vram_gb"] = profile["ram_gb"]  # unified memory
        try:
            out = subprocess.check_output(
                ["system_profiler", "SPHardwareDataType"], text=True, stderr=subprocess.DEVNULL)
            for line in out.splitlines():
                if "Chip" in line or "chip" in line:
                    profile["gpu_name"] = line.split(":")[-1].strip()
                    break
        except Exception:
            profile["gpu_name"] = "Apple Silicon"

    # NVIDIA
    elif shutil.which("nvidia-smi"):
        try:
            out = subprocess.check_output(
                ["nvidia-smi",
                 "--query-gpu=name,memory.total",
                 "--format=csv,noheader,nounits"],
                text=True, stderr=subprocess.DEVNULL)
            lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
            if lines:
                parts = lines[0].split(",")
                profile["gpu_name"] = parts[0].strip()
                profile["gpu_vram_gb"] = round(float(parts[1].strip()) / 1024, 1)
                profile["gpu_type"] = "nvidia"
        except Exception:
            profile["gpu_type"] = "nvidia"

    # AMD ROCm
    elif shutil.which("rocm-smi"):
        try:
            out = subprocess.check_output(
                ["rocm-smi", "--showmeminfo", "vram", "--json"],
                text=True, stderr=subprocess.DEVNULL)
            data = json.loads(out)
            for card in data.values():
                total = card.get("VRAM Total Memory (B)", 0)
                if total:
                    profile["gpu_vram_gb"] = round(int(total) / 1e9, 1)
                    break
            profile["gpu_type"] = "amd"
        except Exception:
            profile["gpu_type"] = "amd"

    return profile


def recommend_models(profile: dict) -> dict:
    """Return recommended LLM and translation models based on hardware profile."""
    vram  = profile["gpu_vram_gb"]
    ram   = profile["ram_gb"]
    gtype = profile["gpu_type"]

    # Effective compute capacity
    if gtype == "apple_silicon":
        capacity = vram  # unified memory
    elif gtype in ("nvidia", "amd"):
        capacity = vram
    else:
        capacity = ram * 0.6  # CPU: use ~60% of RAM conservatively

    # LLM recommendation
    if capacity >= 40:
        llm = ("llama3.1:70b", "llama3.1:70b Q4 — near-GPT-4 quality")
    elif capacity >= 20:
        llm = ("qwen2.5:14b", "Qwen 2.5 14B — excellent multilingual quality")
    elif capacity >= 12:
        llm = ("qwen2.5:14b", "Qwen 2.5 14B — fits with quantization")
    elif capacity >= 8:
        llm = ("qwen2.5:7b", "Qwen 2.5 7B — great balance of quality/speed")
    elif capacity >= 6:
        llm = ("llama3.2:7b", "Llama 3.2 7B — solid multilingual RAG")
    else:
        llm = ("llama3.2:3b", "Llama 3.2 3B — CPU-friendly, good for docs")

    # Translation model recommendation
    if capacity >= 10:
        trans = ("translategemma:12b", "TranslateGemma 12B — higher translation quality")
    else:
        trans = ("translategemma:4b", "TranslateGemma 4B — fast, good quality translation")

    return {"llm": llm, "translation": trans, "capacity_gb": round(capacity, 1)}


def check_ollama() -> dict:
    """Check Ollama installation and running status."""
    status = {
        "installed": shutil.which("ollama") is not None,
        "running":   False,
        "version":   None,
        "models":    [],
        "url":       OLLAMA_URL,
    }

    if status["installed"]:
        try:
            out = subprocess.check_output(["ollama", "--version"], text=True,
                                          stderr=subprocess.STDOUT)
            status["version"] = out.strip().split("\n")[0]
        except Exception:
            pass

    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=2) as r:
            data = json.loads(r.read())
            status["running"] = True
            status["models"]  = [m["name"] for m in data.get("models", [])]
    except Exception:
        pass

    return status


def print_hardware_profile(p: dict, rec: dict):
    W = "\033[0m"; B = "\033[1m"; C = "\033[36m"; G = "\033[32m"; Y = "\033[33m"

    print(f"\n{B}Hardware Profile{W}")
    print(f"  OS       : {p['os_name']} {p['os_release']} ({p['arch']})")
    if p["is_wsl"]:
        print("  WSL      : Yes")
    print(f"  CPU cores: {p['cpu_cores']}")
    print(f"  RAM      : {p['ram_gb']} GB")

    if p["gpu_type"] == "apple_silicon":
        print(f"  GPU      : {G}Apple Silicon{W} ({p.get('gpu_name','M-series')}) — unified memory {p['gpu_vram_gb']} GB")
    elif p["gpu_type"] == "nvidia":
        print(f"  GPU      : {G}NVIDIA{W} {p.get('gpu_name','GPU')} — {p['gpu_vram_gb']} GB VRAM")
    elif p["gpu_type"] == "amd":
        print(f"  GPU      : {Y}AMD ROCm{W} — {p['gpu_vram_gb']} GB VRAM")
    else:
        print(f"  GPU      : {Y}CPU only{W} (no supported GPU detected)")

    print(f"\n{B}Recommendations  (effective capacity: {rec['capacity_gb']} GB){W}")
    print(f"  LLM (RAG)     : {C}{rec['llm'][0]}{W}  — {rec['llm'][1]}")
    print(f"  Translation   : {C}{rec['translation'][0]}{W}  — {rec['translation'][1]}")


def install_ollama_instructions(profile: dict):
    W = "\033[0m"; B = "\033[1m"; Y = "\033[33m"

    if profile["is_wsl"] or profile["os_name"] == "Linux":
        print(f"\n{B}Install Ollama (Linux / WSL):{W}")
        print("  curl -fsSL https://ollama.com/install.sh | sh")
    elif profile["os_name"] == "Darwin":
        print(f"\n{B}Install Ollama (macOS):{W}")
        print("  brew install ollama")
        print("  # or download from https://ollama.com")
    else:
        print(f"\n{B}Install Ollama (Windows):{W}")
        print("  winget install Ollama.Ollama")
        print("  # or download from https://ollama.com")
    print("\n  After installation, start Ollama:")
    print("    ollama serve   # in a separate terminal")
    print(f"  {Y}Note: Ollama runs as a background service on macOS/Windows.{W}")


def pull_model(model: str) -> bool:
    """Pull an Ollama model. Returns True on success."""
    print(f"\n  Pulling {model} …")
    try:
        result = subprocess.run(["ollama", "pull", model])
        return result.returncode == 0
    except Exception as e:
        print(f"  Error pulling model: {e}")
        return False


def interactive_setup():
    W = "\033[0m"; B = "\033[1m"; G = "\033[32m"; C = "\033[36m"; R = "\033[31m"

    print(f"\n{B}{'='*56}")
    print("   Ollama Setup — Course Catalog RAG")
    print(f"{'='*56}{W}")

    profile = detect_hardware()
    rec     = recommend_models(profile)
    ollama  = check_ollama()

    print_hardware_profile(profile, rec)

    print(f"\n{B}Ollama Status{W}")
    if ollama["running"]:
        print(f"  Status  : {G}running{W}  ({ollama['url']})")
        if ollama["version"]:
            print(f"  Version : {ollama['version']}")
        if ollama["models"]:
            print(f"  Models  : {', '.join(ollama['models'])}")
        else:
            print("  Models  : (none installed)")
    elif ollama["installed"]:
        print("  Status  : installed but not running")
        print("  Start   : ollama serve")
    else:
        print(f"  Status  : {R}not installed{W}")
        install_ollama_instructions(profile)
        print("\n  Re-run this script after installing Ollama.")
        return

    # Check if recommended models are already installed
    llm_tag   = rec["llm"][0]
    trans_tag = rec["translation"][0]
    installed = ollama["models"]

    def is_installed(tag):
        return any(m == tag or m.startswith(tag.split(':')[0]) for m in installed)

    print(f"\n{B}Model Setup{W}")

    # LLM
    if is_installed(llm_tag):
        print(f"  {G}✓{W} LLM already installed: {llm_tag}")
    else:
        print(f"\n  Recommended LLM: {C}{llm_tag}{W}")
        print(f"  {rec['llm'][1]}")
        ans = input("  Install? [Y/n]: ").strip().lower()
        if ans in ('', 'y', 'yes'):
            if pull_model(llm_tag):
                print(f"  {G}✓ {llm_tag} installed{W}")
            else:
                print(f"  {R}✗ Failed. Try manually: ollama pull {llm_tag}{W}")
        else:
            print(f"  Skipped. Run later: ollama pull {llm_tag}")

    # Translation
    print(f"\n  Translation model for cross-lingual indexing: {C}{trans_tag}{W}")
    print(f"  {rec['translation'][1]}")
    print("  (Only needed if you enable single-language canonical storage)")
    ans = input("  Install? [y/N]: ").strip().lower()
    if ans in ('y', 'yes'):
        if pull_model(trans_tag):
            print(f"  {G}✓ {trans_tag} installed{W}")
        else:
            print(f"  {R}✗ Failed. Try manually: ollama pull {trans_tag}{W}")
    else:
        print(f"  Skipped. Run later: ollama pull {trans_tag}")

    # Summary
    updated = check_ollama()
    print(f"\n{B}Done.{W}")
    print(f"  Installed models: {', '.join(updated['models']) if updated['models'] else '(none)'}")
    print("\n  Start the backend and open frontend/search-console.html to use the Ask feature.")
    print("  Or run the backend directly:")
    print("    python backend/catalog_server.py --path /your/courses\n")


def main():
    parser = argparse.ArgumentParser(description="Ollama setup helper for Course Catalog RAG")
    parser.add_argument('--detect',        action='store_true', help='Print hardware profile and exit')
    parser.add_argument('--json',          action='store_true', help='Output JSON profile and exit')
    parser.add_argument('--install-model', metavar='MODEL',     help='Pull a specific model non-interactively')
    args = parser.parse_args()

    if args.json:
        profile = detect_hardware()
        rec     = recommend_models(profile)
        ollama  = check_ollama()
        print(json.dumps({
            "hardware": profile,
            "recommendations": rec,
            "ollama": ollama
        }, indent=2))
        return

    if args.detect:
        profile = detect_hardware()
        rec     = recommend_models(profile)
        print_hardware_profile(profile, rec)
        ollama = check_ollama()
        print(f"\nOllama: {'running' if ollama['running'] else 'installed' if ollama['installed'] else 'not installed'}")
        if ollama["models"]:
            print(f"Models: {', '.join(ollama['models'])}")
        return

    if args.install_model:
        if not shutil.which("ollama"):
            print("ERROR: Ollama not installed.")
            sys.exit(1)
        success = pull_model(args.install_model)
        sys.exit(0 if success else 1)

    interactive_setup()


if __name__ == '__main__':
    main()
