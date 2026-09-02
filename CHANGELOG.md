# Changelog

All notable changes to Manga Fill are documented here (Keep a Changelog format).

## [0.14.1] - 2026-09-02

### Fixed
- **Removed the obsolete `./fonts:/app/fonts` volume mount** — it shadowed the image's baked fonts, so the bundled OFL faces (and the build-time-pulled Anime Ace) never reached the container on a fresh deploy, silently falling back to DejaVu. Fonts now ship via the image (OFL committed + Anime Ace pulled at build).

## [0.14.0] - 2026-09-02

### Added
- **Font picker in Settings** — a new "Fonts" section lists every lettering face with a live preview (rendered server-side from the actual font file), its style, and its license. Selecting one persists it and drives typesetting. Default = Anime Ace (manga hand-lettering); four bundled SIL OFL faces (Comic Neue, Bangers, Patrick Hand, Gloria Hallelujah) are committed to the repo as guaranteed fallbacks.
- **Font fallback + greyed-out state** — if the selected font is unavailable (e.g. Anime Ace's build-time pull failed), the app resolves to the next available face and greys the missing entry out in the UI (with an "using X instead" note). Typesetting never fails for lack of a font: selected → default → first bundled → DejaVu Sans Bold.

## [0.13.1] - 2026-09-02

### Changed
- **Font ships via build-time pull, not a manual mount** — the Docker image now pulls Anime Ace (Blambot) into `/app/fonts` during `docker build` (the same pattern as the HuggingFace model weights), so no deploy-time font mount step is needed. The fetch is best-effort with a DejaVu fallback; a licensed face mounted at `/app/fonts` still overrides it.

## [0.13.0] - 2026-09-02

### Added
- **Drop-in manga font** — typesetting now resolves its lettering face automatically: `$MANGA_FILL_FONT` override, then any `.ttf`/`.otf` in `fonts/` (repo) or `/app/fonts` (container volume mount), then DejaVu Sans Bold as fallback. Font files are git-ignored so a licensed face (e.g. CC Wild Words / Anime Ace) can be mounted at runtime without redistributing it — replacing the old hard-coded DejaVu placeholder.

## [0.12.2] - 2026-09-01

### Fixed
- **Default output mode now applies to uploads** — the Settings "Default output mode" (e.g. `mirror`) pre-selects the Upload form's output mode, so a saved default actually takes effect (still overridable per job).

## [0.12.1] - 2026-09-01

### Fixed
- **Upload 500 "database is locked"** — the SQLite engine now runs in WAL mode with a 30s `busy_timeout`, so dashboard polling + worker commits no longer collide. `create_job` also holds the job in an `uploading` state until the archive is fully ingested, so the worker can't claim a half-ingested job.
- **Output filenames preserved** — rendered pages keep the original filename + extension instead of being renumbered to `0000.png`.

## [0.12.0] - 2026-09-01

### Added
- **Mirror input format output mode** — third output option that re-assembles output in the same container as the input: CBZ → CBZ, ZIP → ZIP, folder → folder. `.zip` uploads are now accepted (handled identically to `.cbz`).

### Fixed
- **Streaming upload ingest (OOM)** — `ingest_upload` no longer reads whole files into RAM (`f.file.read()` / `io.BytesIO(...)`). Uploads and CBZ/ZIP archives are staged to disk and expanded in 1 MB chunks, so a large archive can't OOM-kill the container on the swap-less VM — the same class of bug that crashed Subber.
- **`set_setting` idempotency** — a second set of the same key in one session now updates instead of raising a duplicate-key IntegrityError.

## [0.11.0] - 2026-09-01

### Added
- **LLM pricing (peak/off-peak)** — each model now stores peak input/output rates ($/1M tokens) plus an optional off-peak rate + UTC window. The worker reads `usage.prompt_tokens` / `usage.completion_tokens` from the API response and prices each page against the model's rates (peak vs off-peak by current UTC time). Jobs now track `tokens_used` and a real `cost_usd` instead of the always-$0 placeholder.
- **Editable models in Settings** — each model row has a ▾ toggle that expands an inline editor (name, base URL, API key, pricing) with a Save button, plus the existing − remove. The + Add model form now includes the pricing fields.

### Changed
- **Tab state persists across refresh** — the active tab is stored in localStorage and restored on reload (previously a refresh always jumped back to Jobs).

### Fixed
- Model `api_key` no longer hardcoded as the seed default's only field — pricing fields are part of the model dict/CRUD.

## [0.10.0] - 2026-09-01

### Added
- **Model list (model-agnostic)** — translation models are a user-managed list (add/remove from Settings with +/−), each just an OpenAI-compatible `{name, base_url, api_key}`. Pick a model per job at upload; the worker resolves it against the list.

### Changed
- **Removed DeepSeek/OpenRouter-specific config** — no per-provider API-key fields or hardcoded model presets; everything is a generic OpenAI-compatible endpoint. Env config now seeds a single default model (`default_model` / `default_base_url` / `default_api_key`).
- **Fixed tab isolation** — the Settings/Logs panels no longer render on every page (dropped the `#tab-settings { display:flex }` override), and static assets get a `?v=` cache-buster so browsers don't serve stale CSS/JS.

## [0.9.0] - 2026-09-01

### Added
- **Runtime settings** — model selection, API base URL, DeepSeek + OpenRouter API keys, dry-run, and output mode are now editable from the Settings tab and persisted in SQLite (previously model/keys were read-only env values). Changes take effect on the next job.
- **Logs controls** — line-count selector, auto-refresh toggle, and a download link.
- **Docker layer caching** — ML deps moved to a separate cached layer (`requirements-ml.txt`), so a `git pull` + rebuild is seconds rather than a full ~15 min re-download of torch/paddle.

### Changed
- `resolve_translation()` now reads the persisted settings store (model → base URL → matching API key) instead of config env directly.

## [0.8.0] - 2026-09-01

### Added
- **Web UI** — FastAPI + SQLite + single background worker + Jinja2 dashboard: upload (page / batch / CBZ), live job progress, side-by-side viewer, output-mode setting (folder / cbz, leave-as-is default), browser download, logs.
- **Docker build** now installs the ML extras (CPU-only torch/torchvision, transformers, manga-ocr, LaMa, onnxruntime) and patches manga-ocr for transformers>=5.13.

### Fixed
- **Degenerate detector box crash** — a zero-size / off-page `text_free` box cropped to an empty array and crashed manga-ocr's ViT (`shape '[1,3,224,224]' is invalid for input of size 0`); `ocr_crop` now clamps to image bounds and drops zero-area crops.

## [0.7.0] - 2026-08-31

### Added
- **Trained text/bubble detector** (`app/pipeline/detector.py`) — ogkalu RT-DETR-v2 (Apache-2.0) replaces the white-flood-fill bubble heuristic and PP-OCRv5 text detection; returns `bubble` / `text_bubble` / `text_free` in one pass.
- **Free-text translation** — narration boxes, handwritten mutters, and SFX (`text_free`) are now OCR'd + translated + re-lettered instead of left as-is. Only pure-ASCII watermarks/page numbers are skipped.

### Changed
- **Translation: DeepSeek `deepseek-v4-flash`** (was OpenRouter Llama 3.1 8B) — fixes dialogue errors ("You're Izumo... right?" vs "You're really something") and is cheaper. Runtime-configurable via `MANGAFILL_MODEL` / `MANGAFILL_BASE_URL`.
- **Uniform lettering size** — dialogue renders at a consistent page-wide size (~1/48 page width); shrink-to-fit only on overflow. Fixes short lines blowing up to fill big bubbles.
- **Native-resolution inpaint** — LaMa crops + composites only the erased text regions at full resolution instead of downscaling the whole page. Every pixel outside the erased text is byte-identical to the source.
- **Lower detection threshold (0.4 → 0.2)** — catches tiny single-character SFX (ほえ, コヒュ); the OCR + Japanese-content filter rejects false positives.

### Fixed
- **DeepSeek empty-content bug** — v4 models reason by default, burning the `max_tokens` budget and returning empty content on large batches. Fixed by sending `thinking: {"type": "disabled"}`.
- **Duplicate detections** — the detector double-fires some regions; dedup via IoU.

## [0.6.1] - 2026-08-31

### Added

- Bubble detection (`app/pipeline/bubble.py`): recovers each text block's enclosing white speech-bubble / caption-box via a bounded, seed-based flood fill. Guards (min area, overlap, width/height caps, boundary-touch) reject gutter leaks and free-floating text.

### Fixed

- **Blank speech bubbles** — English was re-lettered into the narrow *vertical text column*, which horizontal English can't fit, so `_fit` gave up and drew nothing. Typesetting is now bubble-aware: text is re-lettered into the recovered bubble interior (e.g. Game of Familia's "オード＝シーカ殿…", "御覧の通り…", "あとは…" bubbles now render).
- **Misaligned / garbage text over artwork** — free-floating editorial text (e.g. the 「◎ダバ国を平定…」 teaser over the tower, OCR'd as garbage) was in-painted and re-lettered over the art. Free-floating text (no white container) is now left untouched: its Japanese stays, nothing is drawn over it.
- **Furigana misclassification** — narrow ruby columns up to 16px wide (e.g. 「まほうこっか」) are now classed as furigana instead of vertical dialogue, so they're erased but not re-lettered.
- **Typeset fallback** — a translation that can't fit its box at any size now falls back to a minimum 8px font (slight overflow) instead of silently leaving a blank bubble.

## [0.6.0] - 2026-08-30

### Added

- Inpaint (`app/pipeline/inpaint.py`): LaMa (Apache-2.0, via `simple-lama-inpainting`) erases original text with a solid dilated-box mask.
- Typeset (`app/pipeline/typeset.py`): re-letters English into each box — largest font that fits, word-wrapped, centered. Measurement uses `multiline_textbbox` so it matches actual rendering (no self-overlap).
- End-to-end render (`app/pipeline/render.py`): `render_translated_page()` composes detect → OCR → translate → inpaint → typeset.

### Changed

- Detection: drop isolated single-character OCR results (e.g. an eye read as "し") — artwork noise, never dialogue.

## [0.5.0] - 2026-08-30

### Changed

- Detection: replaced RapidOCR (PP-OCRv4 mobile det) with **PP-OCRv5_server_det** (Apache-2.0, PP-HGNetV2 backbone) via onnxruntime. Recovers free-floating / handwritten vertical text the document-tuned v4 detector missed (e.g. handwritten 「じゃあ黒板消しで…」 over artwork, and the full two-column 「ダバ国とサイファーン国が手を組んだか」 bubble). Classical CV vertical-kernel pass kept as a cheap safety net.

## [0.4.0] - 2026-08-30

### Added

- Translation (`app/pipeline/translate.py`): batch JP→EN via OpenRouter (default `meta-llama/llama-3.1-8b-instruct`), numbered-output parsing (preamble-proof), per-page cost accounting. Furigana + horizontal text (titles/watermarks) skipped.

## [0.3.0] - 2026-08-30

### Changed

- Detection hardened to a hybrid ensemble: RapidOCR (PP-OCR det) + classical CV (adaptive threshold + vertical-line morphology). Catches vertical manga columns the document-tuned detector missed (`死んでも`, `ライクネル`, `そんなに`).
- Block classification: vertical / horizontal / furigana; vertical columns re-merged right-to-left; furigana kept separate; watermarks/titles flagged horizontal.
- ~81% recall on the 3 real JP fixtures (up from ~50%). Remaining gaps: one long-line prefix column + two handwritten lines.

## [0.2.0] - 2026-08-30

### Added

- Headless pipeline core (`app/pipeline/`): ingest, text detection (RapidOCR/onnxruntime, Apache-2.0), OCR (manga-ocr, Apache-2.0), vertical-line merge + reading-order sort.
- Validated against the 3 real JP fixtures: correct OCR for most speech-bubble text (e.g. `てめーの仕事だ！`).

### Notes

- Detection model: RapidOCR (PP-OCR det via onnxruntime). PaddleOCR's native `paddle_static` CPU inference hit a PaddlePaddle 3.3 PIR/oneDNN bug, so we run the same det models through onnxruntime instead.

## [0.1.0] - 2026-08-30

### Added

- Repository scaffold: FastAPI app (`app/main.py`, `/api/health`), pydantic-settings config, Docker Compose + Dockerfile (CPU-first), `config.example.yaml`, `.env.example`, `.gitignore`, Jinja2 dashboard shell.
- Project build plan (separate doc) and real-JP test fixtures.
