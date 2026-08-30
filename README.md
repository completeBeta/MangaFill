# Manga Fill

Translate raw Japanese manga → English. One app, two phases:

- **Phase 1 (in progress):** translation — panel/text detect → OCR → LLM translate → inpaint original text → typeset English back into balloons.
- **Phase 2 (later):** AI colouring (reference-based via AniList material IDs).

## Status

**v0.1.0** — scaffold only. The pipeline is not implemented yet.

## What it will do (per page / feature)

- **Dashboard** — upload raw pages (single image, batch, or CBZ), run a translation job, watch progress.
- **Viewer** — side-by-side original vs translated page; download the translated page from the browser.
- **Settings** — output mode (output folder / re-assemble CBZ / browser download), translation model, dry-run toggle.
- **Logs** — recent processing logs.

## Decisions (locked)

- Input: **web upload** (no watch-dir / Suwayomi integration in v1).
- Automation: **fully automatic, one-shot** — no review step in v1.
- Output: user-selectable; **default = leave-as-is** (no auto re-assembly); CBZ re-assembly is opt-in.
- Translation: **cloud-only via OpenRouter**, model configurable (default start: Gemini Flash). No local-GPU translation.
- Vision GPU: on the homelab GPU host (reserved for inpaint/detect/colour), wired later as a remote worker.

## Run

```bash
cp .env.example .env          # add OPENROUTER_API_KEY
docker compose up -d --build
# dashboard at http://localhost:8788
```

## License

MIT. Manga Fill composes permissive-licensed models (manga-ocr, LaMa — Apache-2.0); it does not fork GPL pipeline apps.
