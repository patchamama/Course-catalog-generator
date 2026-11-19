#!/usr/bin/env python3
"""
Test different yt-dlp configurations to find what works for a given YouTube URL.
Usage: python test_clients.py https://youtu.be/n0I3mgYRsvs
"""

import sys
import yt_dlp

TEST_URL = sys.argv[1] if len(sys.argv) > 1 else "https://youtu.be/n0I3mgYRsvs"

STRATEGIES = [
    {
        "name": "default (no extra args)",
        "opts": {},
    },
    {
        "name": "player_client=web",
        "opts": {"extractor_args": {"youtube": {"player_client": ["web"]}}},
    },
    {
        "name": "player_client=android",
        "opts": {"extractor_args": {"youtube": {"player_client": ["android"]}}},
    },
    {
        "name": "player_client=mweb",
        "opts": {"extractor_args": {"youtube": {"player_client": ["mweb"]}}},
    },
    {
        "name": "player_client=tv_embedded",
        "opts": {"extractor_args": {"youtube": {"player_client": ["tv_embedded"]}}},
    },
    {
        "name": "player_client=web_embedded",
        "opts": {"extractor_args": {"youtube": {"player_client": ["web_embedded"]}}},
    },
    {
        "name": "player_client=ios",
        "opts": {"extractor_args": {"youtube": {"player_client": ["ios"]}}},
    },
    {
        "name": "player_client=mediaconnect",
        "opts": {"extractor_args": {"youtube": {"player_client": ["mediaconnect"]}}},
    },
    {
        "name": "player_client=web_creator",
        "opts": {"extractor_args": {"youtube": {"player_client": ["web_creator"]}}},
    },
    {
        "name": "player_client=web,android (fallback chain)",
        "opts": {"extractor_args": {"youtube": {"player_client": ["web", "android"]}}},
    },
    {
        "name": "player_client=mweb,web (fallback chain)",
        "opts": {"extractor_args": {"youtube": {"player_client": ["mweb", "web"]}}},
    },
]

print(f"Testing URL: {TEST_URL}\n{'='*60}")

results = []

for strategy in STRATEGIES:
    name = strategy["name"]
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        **strategy["opts"],
    }
    print(f"\n[TEST] {name} ... ", end="", flush=True)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(TEST_URL, download=False)
        title = info.get("title", "?")
        fmts = len(info.get("formats", []))
        print(f"OK  title={title!r}  formats={fmts}")
        results.append((name, True, title, fmts))
    except Exception as e:
        short = str(e)[:120]
        print(f"FAIL  {short}")
        results.append((name, False, str(e), 0))

print(f"\n{'='*60}")
print("SUMMARY")
print(f"{'='*60}")
for name, ok, detail, fmts in results:
    status = "✓ OK  " if ok else "✗ FAIL"
    if ok:
        print(f"  {status}  [{name}]  formats={fmts}")
    else:
        print(f"  {status}  [{name}]")

working = [(n, f) for n, ok, _, f in results if ok]
if working:
    best = max(working, key=lambda x: x[1])
    print(f"\nBest strategy: '{best[0]}' ({best[1]} formats)")
else:
    print("\nNo strategy worked. Try updating yt-dlp: pip install -U yt-dlp")
