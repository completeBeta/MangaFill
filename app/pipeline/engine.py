"""The ENGINE client — upstream manga-image-translator, over HTTP.

ARCHITECTURE (option A, 2026-09-23). Manga Fill is the SHELL: jobs, queue,
viewer, logs, fonts, CBZ/PDF, cost. The ENGINE does the pipeline. Upstream's FastAPI
service answers:

    POST /translate/with-form/image   multipart: `image` file + `config` JSON  -> PNG
    POST /queue-size                  the queue depth (used as a liveness probe)

The config JSON is upstream's own shape, built by their web UI at front/app/App.tsx
(`buildTranslationConfig`):

    {"detector":   {"detector": "default", "detection_size": 1536,
                    "box_threshold": 0.7, "unclip_ratio": 2.3},
     "render":     {"direction": "auto"},
     "translator": {"translator": "deepseek", "target_lang": "ENG"},
     "inpainter":  {"inpainter": "default", "inpainting_size": 1024},
     "mask_dilation_offset": 30}

Our defaults mirror the fixed values this app has always used, so switching the engine
on does not silently change behaviour; anything the "Advanced" tab exposes overrides
them. Translator API keys are NOT passed here — upstream reads them from the engine
container's environment (DEEPSEEK_API_KEY / CUSTOM_OPENAI_* — see engine/README.md),
which is why our translator section keeps working exactly as it did.

Every call is logged to engine-calls.log (one line: page, ms, bytes, status) and state
changes go to lifecycle.log, so the Logs page shows what the engine is doing.
"""
from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request
import uuid

from PIL import Image

from app.config import settings
from app.services.logging import engine_call, lifecycle

# Upstream's ranges, straight from their front (front/app/config.ts).
TARGET_LANGUAGES: list[tuple[str, str]] = [
    ("ENG", "English"), ("CHS", "简体中文"), ("CHT", "繁體中文"), ("JPN", "日本語"),
    ("KOR", "한국어"), ("CSY", "čeština"), ("NLD", "Nederlands"), ("FRA", "français"),
    ("DEU", "Deutsch"), ("HUN", "magyar nyelv"), ("ITA", "italiano"), ("POL", "polski"),
    ("PTB", "português"), ("ROM", "limba română"), ("RUS", "русский язык"),
    ("ESP", "español"), ("TRK", "Türk dili"), ("UKR", "українська мова"),
    ("VIN", "Tiếng Việt"), ("ARA", "العربية"), ("CNR", "crnogorski jezik"),
    ("SRP", "српски језик"), ("HRV", "hrvatski jezik"), ("THA", "ภาษาไทย"),
    ("IND", "Indonesia"), ("FIL", "Wikang Filipino"),
]
DETECTION_SIZES = [1024, 1536, 2048, 2560]
INPAINTING_SIZES = [516, 1024, 2048, 2560]
DETECTORS = [("default", "Default"), ("ctd", "CTD"), ("paddle", "Paddle")]
INPAINTERS = [("default", "Default"), ("lama_large", "Lama Large"), ("lama_mpe", "Lama MPE"),
              ("sd", "SD"), ("none", "None"), ("original", "Original")]
RENDER_DIRECTIONS = [("auto", "Auto"), ("horizontal", "Horizontal"), ("vertical", "Vertical")]

# Our long-standing fixed values — the defaults the Advanced tab starts from.
DEFAULT_CONFIG = {
    "detector": {"detector": "default", "detection_size": 1536,
                 "box_threshold": 0.7, "unclip_ratio": 2.3},
    "render": {"direction": "auto"},
    "translator": {"translator": "deepseek", "target_lang": "ENG"},
    "inpainter": {"inpainter": "default", "inpainting_size": 1024},
    "mask_dilation_offset": 30,
}

# ---- Per-source-language presets (MEASURED, 2026-09-24) --------------------------
# A six-arm sweep, 10 pages per language per arm (30 pages/arm), scored by leftover
# source characters per page — the full matrix and the vision checks live in the skill
# at references/engine-mode.md. Summary (chars/page, lower is better):
#
#     arm (detector / detection_size / box_threshold / inpainting)   ja    ko    zh
#     A  default 1536 0.7 inpaint 1024  (what we ship today)         7.9   5.0   4.2
#     B  default 2560 0.7 inpaint 1024                               8.3   2.9   4.8
#     C  default 2560 0.6 inpaint 1024                               7.6   2.8   4.2
#     D  ctd     ----  --- inpaint 1024                              7.4   2.7  14.2  <- trap on zh
#     E  default 1536 0.7 inpaint 2048                               6.8   4.8   4.2
#     F  default 2560 0.6 inpaint 2048                               6.9   2.8   3.9
#
# Korean is the one language with a large, reproducible win: −44% leftover text, which
# C, D and F all agree on (and the zh metric is ~60% site-watermark noise, so the ja/zh
# differences above are small or meaningless). So only Korean gets a preset, and it gets
# arm C's detector settings — F scores identically on ko (28 chars) while taking 26%
# longer per page (18.5 s vs 14.7 s), so the extra inpainting buys nothing there.
#
# Cost on the CPU-only host: ko 9.8 s → ~14.7 s per page. ja/zh keep the long-standing
# defaults (2026-09-24 decision: "ko → F/C, ja+zh → defaults").
PRESETS: dict[str, dict] = {
    "ko": {"detection_size": 2560, "box_threshold": 0.6},
}
PRESET_NAMES: dict[str, str] = {
    "ko": "ko-dense-recall (detection 2560, box 0.6 — measured −44% leftover text)",
}
PRESET_MODES = ("per-language", "off")


# Runtime override set once per job from the settings STORE (the UI edits it without a
# restart); falls back to the env/config values when unset.
_override: dict = {}


def configure(url: str | None = None, enabled_flag: bool | None = None) -> None:
    if url is not None:
        _override["url"] = url
    if enabled_flag is not None:
        _override["on"] = enabled_flag


def base_url() -> str:
    return (_override.get("url") or settings.engine_url or "").rstrip("/")


def enabled() -> bool:
    """True only when the engine is switched on AND has an address."""
    on = _override.get("on")
    if on is None:
        on = settings.engine_enabled
    return bool(on and base_url())


def preset_for(source_lang: str | None, mode: str | None = None) -> dict:
    """The measured overrides for this source language, or {} when none apply.

    `mode` is the stored `engine_preset_mode` ("per-language" | "off"); "off" disables
    presets entirely and leaves the Advanced tab in full manual control. An unknown or
    empty language (the engine auto-detecting) simply has no preset.
    """
    m = (mode if mode is not None else (_override.get("preset_mode") or "per-language"))
    if str(m).strip().lower() == "off":
        return {}
    return dict(PRESETS.get((source_lang or "").strip().lower(), {}))


def preset_name(source_lang: str | None, mode: str | None = None) -> str:
    """Human-readable name of the preset that will be applied ("" when none)."""
    if not preset_for(source_lang, mode):
        return ""
    return PRESET_NAMES.get((source_lang or "").strip().lower(), "preset")


def build_config(options: dict | None = None, source_lang: str | None = None,
                 preset_mode: str | None = None) -> dict:
    """Our stored Advanced options over the defaults, in upstream's request shape.

    A per-language preset, when one exists, is applied LAST so it wins over the stored
    Advanced values — that is the whole point of a measured preset, and the UI says so.
    Set `engine_preset_mode=off` for pure manual control.
    """
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    o = options or {}
    d, r, t, i = cfg["detector"], cfg["render"], cfg["translator"], cfg["inpainter"]
    if o.get("detector"):
        d["detector"] = o["detector"]
    for key, cast in (("detection_size", int), ("box_threshold", float), ("unclip_ratio", float)):
        if o.get(key) not in (None, ""):
            try:
                d[key] = cast(o[key])
            except (TypeError, ValueError):
                pass
    if o.get("render_direction"):
        r["direction"] = o["render_direction"]
    if o.get("inpainter"):
        i["inpainter"] = o["inpainter"]
    if o.get("inpainting_size") not in (None, ""):
        try:
            i["inpainting_size"] = int(o["inpainting_size"])
        except (TypeError, ValueError):
            pass
    if o.get("mask_dilation_offset") not in (None, ""):
        try:
            cfg["mask_dilation_offset"] = int(o["mask_dilation_offset"])
        except (TypeError, ValueError):
            pass
    if o.get("target_lang"):
        t["target_lang"] = o["target_lang"]
    if o.get("translator"):
        t["translator"] = o["translator"]
    # NO source_lang: upstream's Config has no such field — their pipeline detects the
    # source language itself (langid + OCR), and their own front sends only
    # {translator, target_lang}. Sending it was a no-op, and a *wrong* guess here could
    # only mislead a future reader of the logs, so it is not sent at all.
    for key, value in preset_for(source_lang, preset_mode).items():
        if key in d:
            d[key] = value
        else:
            cfg[key] = value
    return cfg


def health(timeout: float = 10.0) -> dict:
    """Liveness + queue depth. Never raises: the dashboard must render when it is down."""
    url = base_url()
    if not url:
        return {"ok": False, "queue": None, "detail": "no engine url configured"}
    try:
        req = urllib.request.Request(f"{url}/queue-size", method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"ok": True, "queue": int(r.read().decode().strip() or 0), "detail": "ok"}
    except urllib.error.HTTPError as e:
        # 404/405 on some revisions: fall back to the OpenAPI page as the probe.
        try:
            with urllib.request.urlopen(f"{url}/docs", timeout=timeout) as r:
                if r.status < 500:
                    return {"ok": True, "queue": None, "detail": f"up (queue probe HTTP {e.code})"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "queue": None, "detail": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "queue": None, "detail": "up"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "queue": None, "detail": f"{type(e).__name__}: {e}"}


def translate_page_regions(image_path: str, config: dict | None = None,
                           timeout: float | None = None,
                           tag: str = "") -> tuple[list[dict], str]:
    """Detect + OCR + translate ONE page and return its regions — NO rendering (v0.30.0).

    This is the hybrid's engine half. Upstream's `/translate/with-form/json` answers with
    their ichigo-inspired structure: one entry per text block carrying the box, the source
    text they read, their translations keyed by language, and their own angle/confidence.
    That is exactly what our typesetter consumes, which is what makes option 1 possible —
    the engine does the reading, this app does the drawing (app/pipeline/render.py).

    Returns `(regions, source_language)`, where regions is a list of dicts:
        {"x0","y0","x1","y1", "text", "translation", "angle", "prob"}
    and `source_language` is ja/ko/zh ("" when the engine didn't say) — the caller needs it
    because our own render path must know the language, and the engine is the authority on
    it. Nothing is rendered here, so the caller keeps our lettering.

    Raises RuntimeError with the engine's own message on failure, same as translate_page.
    """
    url = base_url()
    if not url:
        raise RuntimeError("engine url not configured")
    cfg = config if config is not None else build_config()
    target = ((cfg.get("translator") or {}).get("target_lang") or "ENG").upper()
    with open(image_path, "rb") as fh:
        raw_image = fh.read()
    body, ctype = _multipart({"config": json.dumps(cfg)},
                             {"image": (image_path.split("/")[-1], raw_image, "image/png")})
    # THE STREAM ENDPOINT — NOT /translate/with-form/json. Measured 2026-09-24 on this
    # build: every non-stream route (/json and /image alike) answers HTTP 500
    # "'utf-8' codec can't decode byte 0x80 in position 0" because their parent→child
    # dispatcher reassembles the child's binary reply as text. The /stream twin works,
    # and its `transform_to_json` sends plain `model_dump_json()` bytes as the status-0
    # frame, so the payload parsed below is exactly the TranslationResponse.
    import struct

    req = urllib.request.Request(f"{url}/translate/with-form/json/stream", data=body,
                                 headers={"Content-Type": ctype}, method="POST")
    t0 = time.perf_counter()
    result_json: bytes | None = None
    stage = ""
    try:
        with urllib.request.urlopen(req, timeout=timeout or settings.engine_timeout_s) as r:
            while True:
                head = r.read(5)
                if len(head) < 5:
                    break
                f_status = head[0]
                f_size = struct.unpack(">I", head[1:5])[0]
                chunk = r.read(f_size) if f_size else b""
                if f_status == 0:                      # the JSON result
                    result_json = chunk
                elif f_status == 1:                    # progress report
                    stage = chunk.decode("utf-8", "replace").strip()
                    engine_call("%s %s: stage %s", url, tag, stage)
                elif f_status == 2:                    # error report
                    raise RuntimeError((chunk.decode("utf-8", "replace") or "engine error")[:400])
                # 3 = queue position, 4 = waiting for a translator instance: informational
    except urllib.error.HTTPError as e:
        detail = e.read()[:400].decode("utf-8", errors="replace")
        engine_call("FAIL %s %s -> HTTP %s (%s) %s", url, tag, e.code,
                    f"{time.perf_counter()-t0:.0f}ms", detail)
        lifecycle("engine regions failed: %s", detail)
        raise RuntimeError(f"engine HTTP {e.code}: {detail}") from None
    except RuntimeError:
        engine_call("FAIL %s %s -> engine error frame at stage %r", url, tag, stage)
        lifecycle("engine regions failed (%s): error frame", tag or image_path)
        raise
    except Exception as e:  # noqa: BLE001
        engine_call("FAIL %s %s -> %s: %s", url, tag, type(e).__name__, e)
        raise RuntimeError(f"engine unreachable: {type(e).__name__}: {e}") from None
    if not result_json:
        engine_call("FAIL %s %s -> stream ended with no JSON (last stage %r)", url, tag, stage)
        raise RuntimeError(f"engine stream ended without regions (last stage {stage!r})")
    try:
        data = json.loads(result_json.decode("utf-8", errors="replace"))
    except Exception as e:  # noqa: BLE001
        engine_call("FAIL %s %s -> unparseable JSON (%s)", url, tag, e)
        raise RuntimeError(f"engine returned unparseable JSON: {e}") from None
    regions = _regions_from_engine_json(data, target)
    src_lang = lang_from_engine_json(data, target)
    engine_call("OK %s %s -> regions=%d source=%s %.0fms", url, tag, len(regions),
                src_lang or "?", (time.perf_counter() - t0) * 1000)
    return regions, src_lang


# upstream's source-language codes → our language ids. MEASURED on this build (2026-09-24):
# their blocks key the text with LOWERCASE ISO-639-1 ("ko", "ja", "zh") — their older UI used
# uppercase ISO-639-3 ("JPN", "KOR", "CHS"). Accept BOTH: the first version of this map knew
# only the uppercase form, reported no language, and left a colour Korean page untouched.
_ENGINE_CODE_TO_LANG = {
    "ja": "ja", "ko": "ko", "zh": "zh", "zh-cn": "zh", "zh-tw": "zh",
    "zh-hans": "zh", "zh-hant": "zh", "ja-jp": "ja", "ko-kr": "ko",
    "JPN": "ja", "KOR": "ko", "CHS": "zh", "CHT": "zh",
}


def lang_from_engine_json(data: dict, target: str) -> str:
    """The source language the ENGINE decided on, read from its per-block text keys.

    WHY THIS EXISTS (found on the first hybrid job, 2026-09-24): when the source language
    was left on auto and the memory-guarded probe declined to run, the job reached the
    render stage with no language, our pipeline fell back to "ja" — and the Japanese path
    SKIPS colour pages outright. So job 29 (a colour Korean webtoon page) came back
    byte-identical with 0 blocks while the engine had correctly read 2 Korean regions.
    The engine had the answer all along: every block's `text` map is keyed by the language
    it OCR'd, so the majority key is the source language. This is that answer, for free.
    """
    counts: dict[str, int] = {}
    for item in (data or {}).get("translations") or []:
        texts = (item or {}).get("text") if isinstance(item, dict) else None
        if not isinstance(texts, dict):
            continue
        for code in texts:
            if code in _ENGINE_CODE_TO_LANG and code != target:
                counts[code] = counts.get(code, 0) + 1
    if not counts:
        return ""
    return _ENGINE_CODE_TO_LANG[max(counts, key=lambda c: counts[c])]


# Source-language codes upstream may key a block's text with, in the order we prefer when a
# block carries several non-target entries. MEASURED 2026-09-24: the live response for a Korean
# page was {"ENG": "...", "ko": "..."} — lowercase ISO-639-1 — so both shapes are listed.
_SOURCE_CODES = ("JPN", "KOR", "CHS", "CHT", "ja", "ko", "zh")


def _regions_from_engine_json(data: dict, target: str) -> list[dict]:
    """Map upstream's TranslationResponse into our region dicts.

    Each block's `text` is a {language_code: string} map holding the source string AND
    every translation it produced. The source is the non-target entry (preferring a known
    source code); the translation is the target entry, and a block missing either is
    dropped here — `_blocks_from_engine_regions` would drop it anyway, but dropping it at
    the boundary keeps the log line's count honest about what is drawable.
    """
    out: list[dict] = []
    for item in (data or {}).get("translations") or []:
        if not isinstance(item, dict):
            continue
        texts = item.get("text") or {}
        if not isinstance(texts, dict):
            continue
        translation = (texts.get(target) or "").strip()
        source = ""
        for code in _SOURCE_CODES:
            if code != target and texts.get(code):
                source = str(texts[code]).strip()
                break
        if not source:
            for code, value in texts.items():
                if code != target and value:
                    source = str(value).strip()
                    break
        if not source or not translation or translation == source:
            continue
        try:
            out.append({
                "x0": int(item.get("minX", 0)), "y0": int(item.get("minY", 0)),
                "x1": int(item.get("maxX", 0)), "y1": int(item.get("maxY", 0)),
                "text": source, "translation": translation,
                "angle": item.get("angle") or 0.0, "prob": item.get("prob"),
            })
        except (TypeError, ValueError):
            continue
    return out


def _multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    """Minimal multipart/form-data encoder (no requests/httpx dependency)."""
    boundary = f"----mangafill{uuid.uuid4().hex}"
    out = io.BytesIO()
    for name, value in fields.items():
        out.write(f"--{boundary}\r\n".encode())
        out.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        out.write(str(value).encode("utf-8"))
        out.write(b"\r\n")
    for name, (filename, payload, ctype) in files.items():
        out.write(f"--{boundary}\r\n".encode())
        out.write(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode())
        out.write(f"Content-Type: {ctype}\r\n\r\n".encode())
        out.write(payload)
        out.write(b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def translate_page(image_path: str, config: dict | None = None,
                   timeout: float | None = None, tag: str = "") -> Image.Image:
    """Send one page to the engine and return the translated page as a PIL image.

    Raises RuntimeError with the engine's own message on failure — the caller (the job
    engine) marks the page failed and the log line says which page and why.
    """
    url = base_url()
    if not url:
        raise RuntimeError("engine url not configured")
    cfg = json.dumps(config if config is not None else build_config())
    with open(image_path, "rb") as fh:
        payload = fh.read()
    body, ctype = _multipart({"config": cfg}, {"image": (image_path.split("/")[-1], payload, "image/png")})
    # THE ENDPOINT THAT WORKS (measured 2026-09-23 against upstream 95227a2b):
    #   * /translate/with-form/image        -> HTTP 500 "'utf-8' codec can't decode byte
    #     0x80" while their dispatcher reassembles the child instance's binary reply
    #   * /translate/with-form/image/stream/web -> 69-byte 1x1 placeholder PNG (it exists
    #     to drive their own React UI, which then fetches result/<id>/final.png)
    #   * /translate/with-form/image/stream -> frames of 1 byte status + 4 byte size +
    #     data: progress reports, then the finished image. Documented as the API/script
    #     path, and it doubles as our per-page stage log.
    import struct

    req = urllib.request.Request(f"{url}/translate/with-form/image/stream", data=body,
                                headers={"Content-Type": ctype}, method="POST")
    t0 = time.perf_counter()
    data: bytes | None = None
    stage = ""
    try:
        with urllib.request.urlopen(req, timeout=timeout or settings.engine_timeout_s) as r:
            while True:
                head = r.read(5)
                if len(head) < 5:
                    break
                f_status = head[0]
                f_size = struct.unpack(">I", head[1:5])[0]
                chunk = r.read(f_size) if f_size else b""
                if f_status == 0:                      # result image
                    data = chunk
                elif f_status == 1:                    # progress report
                    stage = chunk.decode("utf-8", "replace").strip()
                    engine_call("%s %s: stage %s", url, tag, stage)
                elif f_status == 2:                    # error report
                    raise RuntimeError((chunk.decode("utf-8", "replace") or "engine error")[:400])
                # 3 = queue position, 4 = waiting for a translator instance: informational
    except urllib.error.HTTPError as e:
        detail = e.read()[:400].decode("utf-8", errors="replace")
        engine_call("FAIL %s %s -> HTTP %s (%s) %s", url, tag, e.code, f"{time.perf_counter()-t0:.0f}ms", detail)
        lifecycle("engine page failed: %s", detail)
        raise RuntimeError(f"engine HTTP {e.code}: {detail}") from None
    except RuntimeError:
        engine_call("FAIL %s %s -> engine error frame at stage %r", url, tag, stage)
        lifecycle("engine page failed (%s): error frame", tag or image_path)
        raise
    except Exception as e:  # noqa: BLE001
        engine_call("FAIL %s %s -> %s: %s", url, tag, type(e).__name__, e)
        lifecycle("engine unreachable for %s: %s", tag or image_path, e)
        raise RuntimeError(f"engine unreachable: {type(e).__name__}: {e}") from None
    ms = (time.perf_counter() - t0) * 1000
    if not data:
        engine_call("FAIL %s %s -> stream ended with no image (last stage %r)", url, tag, stage)
        raise RuntimeError(f"engine stream ended without an image (last stage {stage!r})")
    img = Image.open(io.BytesIO(data))
    img.load()
    engine_call("OK %s %s -> stream bytes=%d %.0fms (last stage %s)", url, tag, len(data), ms, stage)
    return img.convert("RGB")
