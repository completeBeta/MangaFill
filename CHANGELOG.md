# Changelog

All notable changes to Manga Fill are documented here (Keep a Changelog format).

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
