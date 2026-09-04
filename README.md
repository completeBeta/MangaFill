# Manga Fill

Translate raw Japanese manga → English. One app, two phases:

- **Phase 1 (in progress):** translation — panel/text detect → OCR → LLM translate → inpaint original text → typeset English back into balloons.
- **Phase 2 (later):** AI colouring (reference-based via AniList material IDs).

## Status

**v0.6.0** — full pipeline runs end-to-end on a page (detect → OCR → translate → inpaint → typeset):

- **Detect** — PP-OCRv5 server det (Apache-2.0, ONNX) + classical CV vertical-kernel safety net.
- **OCR** — manga-ocr (Apache-2.0), vertical (縦書き) native.
- **Translate** — OpenRouter, batched JP→EN.
- **Inpaint** — LaMa (Apache-2.0) erases the original text.
- **Typeset** — English re-lettered into each box, sized/wrapped to fit.

Still to build: web UI, batch/state, deploy. Known limits: bubble-aware typesetting (English in narrow vertical bubbles stacks into many short lines), furigana re-separation under PP-OCRv5 boxes.

## What it will do (per page / feature)

- **Dashboard** — upload raw pages (single image, batch, or CBZ), run a translation job, watch progress.
- **Viewer** — side-by-side original vs translated page; download the translated page from the browser.
- **Settings** — output mode (output folder / re-assemble CBZ / browser download), translation model, dry-run toggle, font, and GPU device (local auto-detect / remote worker URL / CPU).
- **Logs** — recent processing logs.

## Decisions (locked)

- Input: **web upload** (no watch-dir / Suwayomi integration in v1).
- Automation: **fully automatic, one-shot** — no review step in v1.
- Output: user-selectable; **default = leave-as-is** (no auto re-assembly); CBZ re-assembly is opt-in.
- Translation: **cloud-only via DeepSeek** (`deepseek-v4-flash` default), runtime-configurable via `MANGAFILL_MODEL`/`MANGAFILL_BASE_URL`. No local-GPU translation.
- Vision GPU: runs locally (CUDA/ROCm) when the host has one, or offloads to a remote GPU worker (NVIDIA / AMD / CPU variants — see the [GPU worker setup guide](gpu-worker/SETUP_GUIDE.md)); reserved for inpaint/detect, never translation.

## Run

```bash
cp .env.example .env          # add your model API key (MANGA_FILL_DEFAULT_API_KEY)
docker compose up -d --build
# dashboard at http://localhost:8788
```

## License

MIT. Manga Fill composes permissive-licensed models (manga-ocr, LaMa — Apache-2.0); it does not fork GPL pipeline apps.
