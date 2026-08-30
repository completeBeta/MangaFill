# Changelog

All notable changes to Manga Fill are documented here (Keep a Changelog format).

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
