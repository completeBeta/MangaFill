# Changelog

All notable changes to Manga Fill are documented here (Keep a Changelog format).

## [0.22.0] - 2026-09-05

### Added
- **Korean + Chinese OCR routes through the GPU worker** — when a worker URL is configured, `ko`/`zh` pages offload their PaddleOCR to the worker's new `/ocr-multilingual` endpoint (same PP-OCRv5/v6, same results), freeing the app's memory-constrained CPU for the rest of the pipeline. Falls back to the app's local PaddleOCR if the worker is unreachable. The GPU worker is bumped to 0.3.0 with the PaddleOCR (onnxruntime) stack.

## [0.21.0] - 2026-09-05

### Changed
- **Korean + Chinese OCR: EasyOCR → PaddleOCR (PP-OCRv5/v6)** — EasyOCR is horizontal-tuned and mangled vertical manhua/manhwa text (it read the vertical 作揖 as a single wrong character at 0.003 confidence). PaddleOCR reads vertical text via its built-in textline-orientation classifier and is measurably more accurate on hangul (있기/될까요/물론이죠 vs EasyOCR's 잎기/훨까요/물론이조). Runs on CPU through the ONNX runtime engine (paddle native CPU inference is broken — PIR/oneDNN crash).
- **Language auto-detection is now kana/hangul-range + confidence** — PaddleOCR's `ch` model reads hanzi AND kana in one model, so detection probes `korean` then `ch` (one pipeline held at a time) and classifies by hangul/kana/confidence. Simpler and more robust than the old three-reader confidence sweep.
- **Dropped EasyOCR dependency** — removed from `requirements-ml.txt` + `pyproject.toml` (frees its torch model memory on the memory-constrained hosts).

## [0.20.0] - 2026-09-05

### Added
- **Multi-language input (Korean + Chinese)** — the pipeline now auto-detects the source language (`auto` default, or force `ja`/`ko`/`zh` in Settings) and routes OCR accordingly: Japanese keeps manga-ocr (GPU worker or local), Korean and Chinese use EasyOCR (Apache-2.0, local CPU). Detection is by OCR confidence (the wrong-language reader emits its script at near-zero confidence). The translation prompt is now language-aware, and the source-text gate accepts hangul in addition to kana/kanji.
- **Colour webtoon/manhua pages are now translated** — the old `_is_color` skip (which protected B/W manga covers from mangling) applied only to Japanese; Korean webtoons and Chinese manhua are coloured by design and now run the full pipeline.

### Known limitations (this release)
- Vertical Korean/Chinese text (historical woodcuts, decorative manhua scrolls) OCRs poorly — EasyOCR is horizontal-tuned; a rotation/vertical pass is a future refinement.
- Tiny single-character utterances and SFX are still skipped (same class as the existing Japanese single-char miss).

## [0.19.6] - 2026-09-05

### Fixed
- **Table-of-contents / chapter-title pages left untranslated** — a pure-horizontal page carrying chapter headings (第N話/章/回/編/節) is now treated as a TOC or chapter-title page and its horizontal text (chapter numbers + titles) is translated, instead of being skipped like a cover/credit page. Cover/credit pages (no chapter numbers) are still left as-is.

## [0.19.5] - 2026-09-05

### Fixed
- **Download round-trips the upload format** — a `.cbz` upload previously downloaded as `.zip` (comic readers won't open a file named `.zip`). The download endpoint now serves the extension matching the output mode and upload format (`.cbz` → `.cbz`, `.zip` → `.zip`, folder uploads → `.cbz`), instead of always zipping as `.zip`.

## [0.19.4] - 2026-09-04

### Fixed
- **Title/header text garbled** — manga-ocr misreads large decorative title lettering (月が導く異世界道中 OCR'd as 日道異世界中の建築), producing nonsense "translations". Large horizontal titles/headers (taller than ~15% of the page AND wider than tall) are now skipped — series titles and section headers are proper-noun logos that stay as-is, while tall-narrow bio paragraphs are still translated.
- **Stat columns wrapped as one paragraph** — bulleted stat text (●筋力Ｂ＋●持久力Ｂ…) is split into per-line blocks so each stat typesets on its own line instead of wrapping awkwardly.
- **More LLM refusal markers** — `[Unintelligible text-likely OCR corruption]` and similar bare refusal forms are now dropped instead of typeset onto the page.

## [0.19.3] - 2026-09-04

### Fixed
- **Literal refusal text leaked onto the page** — when the model can't read garbled OCR it sometimes returns a bare/hyphenated refusal like `(Garbled text-unable to translate meaningfully)` that the placeholder filter didn't catch. Added the bare `unable to translate` / `cannot translate` / `garbled text` / `meaningless` markers so these are dropped instead of typeset.

## [0.19.2] - 2026-09-04

### Fixed
- **Text layered on top of itself on title/stat pages** — the GPU worker returned the same text region at several granularities (a whole stat column plus its individual lines, the same credit line twice) and marked every block "vertical", so the typesetter re-lettered English on top of English. The worker now classifies orientation by box shape and dedups nested/overlapping detections (vertical keeps the full region, horizontal keeps individual lines); the app additionally dedups overlapping blocks as a safety net.
- **Text spilling over its box (padding)** — the typesetter measured the glyph box without the white-outline stroke, so lettering + outline overflowed the bubble/box. Measurement now includes the stroke (`textbbox`/`multiline_textbbox` with `stroke_width`), and box padding was increased.

### Changed
- GPU worker `/detect-ocr` orientation classification + nested-box dedup (worker 0.2.2).

## [0.19.1] - 2026-09-04

### Fixed
- **Already-English pages re-translated and re-lettered on top of themselves** — the "is this Japanese?" gate was applied to free text but not speech-bubble text, and not at all on the remote GPU-worker path, so pre-translated pages (covers, TOC, character intros) got OCR'd and "translated" again, painting English over existing English. Blocks whose OCR text has no kana/kanji are now dropped (`_drop_non_japanese`) at the pipeline choke-point, covering the local detector, the remote GPU worker, and the PP-OCR fallback. An all-English page now yields zero blocks and is returned byte-for-byte unchanged.
- **Empty-box ("tofu") glyphs in typeset text** — translations carrying smart punctuation (curly quotes, em/en-dashes, ellipses) or accented Latin had no glyph in the comic fonts and rendered as hollow rectangles. Translations are now normalized to plain ASCII (`_normalize_ascii`: NFKD decomposition + typographic-punctuation mapping + non-ASCII strip) before typesetting.

### Changed
- GPU worker `/detect-ocr` now drops non-Japanese speech-bubble text (worker 0.2.1), matching the app.

## [0.19.0] - 2026-09-04

### Added
- **App image on GHCR** — the Manga Fill app now builds and publishes to `ghcr.io/completebeta/manga-fill-app` (`latest` + `latest-v0.19.0`) via a new GitHub Actions workflow (`.github/workflows/app.yml`), so it can be pulled on Unraid or any Docker host without building: `docker pull ghcr.io/completebeta/manga-fill-app:latest`. The app container is CPU-first; GPU acceleration comes from the separate GPU worker image.

## [0.18.4] - 2026-09-04

### Changed
- **GPU worker guide discoverability** — linked `gpu-worker/SETUP_GUIDE.md` from the main README and from the in-app Settings → GPU section (a "GPU worker setup guide ↗" link), so users find the setup instructions where they need them. The Pascal (Unraid P2000) step in the guide is now self-contained.

## [0.18.3] - 2026-09-04

### Changed
- **GPU worker: NVIDIA images reorganized** — the default `Dockerfile` is now the modern-NVIDIA image (RTX 20/30/40/50, torch 2.7.1 + CUDA 12.8), and Pascal (P2000 / GTX 10-series) moved to a dedicated `Dockerfile.pascal` (torch 2.5.1 + CUDA 12.4). The separate `Dockerfile.blackwell` is folded into the default. RTX 30/40/50 now share one image; the P2000 is the lone legacy case.

### Added
- **GPU worker setup guide** (`gpu-worker/SETUP_GUIDE.md`) — a step-by-step walkthrough for every GPU type (NVIDIA RTX, NVIDIA Pascal, AMD, CPU): identifying your card, prerequisites, build/run (compose + Unraid Docker UI), wiring into Manga Fill, verification, and troubleshooting.

## [0.18.2] - 2026-09-04

### Added
- **GPU worker: NVIDIA Blackwell (RTX 50-series) image** — a second NVIDIA variant (`Dockerfile.blackwell`, `pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime`) for RTX 50 cards, whose sm_120 arch needs CUDA 12.8 / PyTorch 2.7+. The default image stays on 2.5.1 for Pascal (P2000); RTX 20/30/40 already work on the default image.

## [0.18.1] - 2026-09-04

### Changed
- **GPU worker: AMD support broadened to RDNA2/RDNA3/RDNA4** — the ROCm image now bases on `rocm/pytorch:rocm7.0.2` + PyTorch 2.9.1 (up from ROCm 6.4 + 2.5.1). PyTorch 2.9's ROCm wheels ship `gfx1030` kernels, so one image now covers RX 6000 (RDNA2), RX 7000 (RDNA3), and RX 9000 (RDNA4) — plus Instinct MI200/MI300. RDNA1 (RX 5000, gfx1010) stays unsupported: ROCm has no compute build for it.

## [0.18.0] - 2026-09-04

### Added
- **GPU worker: vendor variants** — the standalone worker now ships three flavours so it runs on any GPU (or none): NVIDIA (`Dockerfile`, CUDA 12.4), AMD (`Dockerfile.rocm`, ROCm 6.4), and CPU (`Dockerfile.cpu`). `GET /health` additionally reports `backend` (`cuda` / `rocm` / `cpu`). Torch stays pinned to 2.5.1 across all three for identical model behaviour.
- **Device selector in Settings → GPU** — the local vision device (`auto` / `cuda` / `cpu`) is now user-selectable in the UI (previously config-only), alongside the remote worker URL. Changes apply immediately.

### Changed
- **Settings → GPU reflects all three modes** — the section now exposes local GPU auto-detect, a remote GPU worker URL, and CPU, instead of implying the only option was a remote worker or CPU. The status badge reports the resolved device and vendor (e.g. "Local GPU (ROCm)").
- **Favicon** — added a site/tab icon.

### Fixed
- **GPU status vendor** — `backend` distinguishes NVIDIA CUDA from AMD ROCm so an AMD host isn't mislabelled.

## [0.17.0] - 2026-09-03

### Added
- **Horizontal text translation** — stat/character pages (character names, `筋力 B+` stat values, weapon lines) are now translated and re-lettered at their original position instead of being left in Japanese. Free text is split by orientation before de-duplication: vertical columns keep the full region (re-merge fragments), horizontal lines keep the individual line (drop the containing box), so a two-column stat table no longer collapses into overlapping text. Horizontal text is translated only on pages that also have vertical content — pure cover/credit pages (titles + credits only) stay as-is.
- **Local GPU support** — the vision models (detect / OCR / inpaint) now respect a `device` setting (`auto` / `cpu` / `cuda`, default `auto`): when the host has CUDA the models run on the local GPU directly, with no external worker needed. The external `gpu_worker_url` worker remains available as a third mode. The GPU status badge in Settings now reports "Local GPU" / "External GPU" / "CPU" correctly.

## [0.16.3] - 2026-09-03

### Added
- **Granular, stage-level progress** — the dashboard progress bar now advances through each page's pipeline stages (`Detecting → Reading text → Translating → Cleaning → Typesetting`) instead of jumping a whole page at a time, and shows the live stage + page number while a job runs. The worker records the current `stage` on the job row as the page progresses.

### Fixed
- **Silently-dropped translation lines are now retried** — a batched translation request occasionally drops a line (truncation near `max_tokens`, or numbering drift), which left that bubble untranslated with no error. Any line that comes back empty despite having source text is retried individually before the page is considered done.

## [0.16.2] - 2026-09-03

### Fixed
- **Stat tables / title pages no longer render as overlapping "double-vision" text** — the ogkalu detector path (`render.py`) was forcing every detected region to `vertical` and OCR'ing nested duplicate boxes (a whole box plus its sub-lines) multiple times. Free text is now classified by shape (tall-narrow = vertical dialogue/name columns → translated; wide = horizontal stat lines / titles / credits → left as-is), and nested/overlapping detections are collapsed to the largest region before OCR.
- **Blank and colour pages are left untouched** — dividers/blank pages and colour splash/cover pages (which the B/W pipeline would otherwise erase or mangle) are now detected and copied through byte-for-byte instead of being run through inpaint/typeset.

## [0.16.1] - 2026-09-03

### Fixed
- **Garbage LLM output is no longer typeset** — the translator now drops placeholder/refusal markers (e.g. a literal `[TEXT UNTRANSLATABLE]`), empty lines, and text that is still Japanese (the model echoing the source back instead of translating). The affected block keeps an empty translation, so the typesetter leaves the original Japanese intact instead of painting garbage onto the page.
- **Text is now readable over dark boxes** — typeset lettering draws a white outline behind the glyphs (sized to the font), so black dialogue stays legible over dark screentone/stat panels instead of vanishing.

## [0.16.0] - 2026-09-02

### Added
- **GPU worker** (`gpu-worker/`) — a standalone FastAPI service that runs the vision models (RT-DETR detect, manga-ocr, LaMa inpaint) on an NVIDIA GPU and exposes them over HTTP (`/detect-ocr`, `/inpaint`, `/health`). Ships its own Dockerfile (pinned to PyTorch 2.5.1 + CUDA 12.4 for Pascal support), docker-compose.yml, and a step-by-step Unraid README. Models are baked in at build time so first request is instant.
- **Client-side GPU offload** — the pipeline now calls the worker for detect+OCR and inpaint when `gpu_worker_url` is set, and silently falls back to the local CPU models on any failure (a down GPU never breaks a job).

## [0.15.4] - 2026-09-02

### Fixed
- **Logs page was empty after a page refresh** — the dashboard restored the *active tab* from `localStorage` on reload but never re-fetched the log content, so refreshing while on the Logs tab showed a blank view. The boot sequence now reloads logs when the Logs tab is the restored tab.

## [0.15.3] - 2026-09-02

### Changed
- **Dynamic per-box lettering** — the typeset size is now computed per bubble (largest size that fits that box, capped at ~1/32 of page width) instead of a single uniform page-wide size. Short lines fill big bubbles; long dialogue shrinks to fit small ones.

## [0.15.2] - 2026-09-02

### Changed
- **Larger lettering size** — the uniform typeset size increased from ~1/48 to ~1/42 of page width (a 1125px page now renders ~27px instead of ~23px), so dialogue reads larger and closer to a professional scanlation face. Shrink-to-fit on overflow is unchanged.

## [0.15.1] - 2026-09-02

### Fixed
- **GPU worker URL example uses a generic hostname** (`gpu-host`) instead of a hard-coded internal IP.

## [0.15.0] - 2026-09-02

### Added
- **Job controls** — Start/Resume, Pause, and Stop (cancel) buttons per job, plus a "Clear all" button in the Jobs toolbar. The worker respects pause/cancel between pages; paused/cancelled/failed jobs can be restarted (already-translated pages are skipped on resume).
- **1-week retention purge** — the worker deletes jobs (DB rows + on-disk files) older than 7 days, hourly.
- **GPU section in Settings** — shows the current device plus a GPU worker URL field with a live reachability check (CPU only / connected / unreachable). The vision GPU (detect/OCR/inpaint) can be wired to a remote worker later; translation stays cloud-only.

### Fixed
- **delete_job now removes the job's files on disk** (previously it leaked original/output pages).

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
