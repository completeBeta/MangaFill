"""Probe a job's source language in a CHILD process.

Why a child process: `detect_language` loads PaddleOCR (1–3 GB resident). Running it
inside the web process is exactly what OOM-killed uvicorn at anon-rss 4.05 GB on
2026-09-23 while the CPU engine's own models were resident on this 7.9 GB host (see the
skill's references/engine-mode.md). A child returns its memory to the OS the moment it
exits, so the probe cannot accumulate in the long-lived web process.

The app needs this value in ENGINE mode for one reason: the per-source-language presets
(engine.PRESETS) are keyed on the source language, and upstream's own pipeline detects
the language internally without ever reporting it back. When the user has picked a
language explicitly, nothing here runs.

Never raises. An unreadable page, a missing model, an OOM or a timeout all return None —
the job then simply runs on the engine's own defaults, which is what it did before.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

# MEASURED on .26 (2026-09-24) with the CPU engine holding its models (2.70 GB resident):
# the probe's child peaked at ~1.9 GB RSS, the app container went 455 MB → 2.35 GB, and host
# *available* memory bottomed out at **1295 MB** before recovering to 2669 MB. Nothing was
# killed (engine restarts=0, app restarts=0). So the requirement is roughly the child's 1.9 GB
# peak plus ~0.6 GB of headroom: below that we skip the probe rather than gamble the engine's
# models, which is exactly the gamble that OOM-killed this host before.
MIN_FREE_GB = 2.5

_CHILD = r"""
import json, sys
sys.path.insert(0, "/app")
from PIL import Image
from app.pipeline.ocr_multilingual import detect_language

with Image.open(sys.argv[1]) as im:
    lang = detect_language(im.convert("RGB"))
print(json.dumps({"lang": lang}))
"""


def mem_available_gb() -> float | None:
    """Host free memory, or None when it cannot be read."""
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1048576.0
    except Exception:  # noqa: BLE001
        return None
    return None


def probe(image_path: str, timeout: float = 300.0,
          min_free_gb: float = MIN_FREE_GB) -> tuple[str | None, str]:
    """Return (lang | None, why). `why` is a short reason string for the log line.

    `lang` is one of ja/ko/zh on success; anything else (including a low-memory skip or a
    killed child) returns None with an explanation the caller can log verbatim.
    """
    if not image_path or not os.path.exists(image_path):
        return None, "no source page to probe"
    free = mem_available_gb()
    if free is not None and free < min_free_gb:
        return None, f"skipped: only {free:.1f} GB free (need {min_free_gb:.1f} GB)"
    try:
        proc = subprocess.run([sys.executable, "-c", _CHILD, image_path],
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout:.0f}s"
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        tail = detail[-1][:120] if detail else f"exit {proc.returncode}"
        # 137 = SIGKILL, i.e. the kernel took the child for memory: the guard worked,
        # the web process is intact, and the job continues on defaults.
        return None, f"child exited {proc.returncode} ({tail})"
    for line in reversed((proc.stdout or "").strip().splitlines()):
        try:
            lang = json.loads(line).get("lang")
        except Exception:  # noqa: BLE001
            continue
        if lang in ("ja", "ko", "zh"):
            return lang, f"probed {lang} in a child process"
        return None, f"child reported unusable language {lang!r}"
    return None, "child produced no parseable result"
