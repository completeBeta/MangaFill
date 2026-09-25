# Manga Fill

Translate raw manga, manhwa and manhua pages into English — detect → read → translate →
inpaint the source text away → typeset English back into the balloons — as a self-hosted
web app.

Two halves, deliberately: the engine (upstream
[`manga-image-translator`](https://github.com/zyddnys/manga-image-translator)) reads and
translates the page, and Manga Fill draws the result with its own typesetter — text fitted to
each balloon's outline and filling it, which is the part that makes a page look finished rather
than machine-annotated. Either half can be swapped in Settings, so the app is useful without the
engine and comparable against it.

## Status

**v0.30.7** — the full pipeline, the web UI, engine mode and the measured presets are all in
place and running in production; the log line of every job says which path and which values it
used. What the pipeline is made of:

- **Read** — the engine's detector + OCR (upstream's own models), or this app's own
  (PP-OCRv5 server det + classical CV safety net for detection; manga-ocr for Japanese,
  PaddleOCR for Korean/Chinese).
- **Translate** — the engine's translator, or any OpenAI-compatible endpoint configured under
  Settings → Models, batched per page.
- **Inpaint** — LaMa (Apache-2.0) erases the source text; the engine can do it instead.
- **Typeset** — English re-lettered into each balloon, sized and wrapped to fit its shape, with
  a legibility fallback face when a balloon is too small for the display face.

**Nothing is invented.** The translation prompt is upstream's own, including its rule that
unreadable text is *"output as it is"* rather than guessed at: a panel-clipped line keeps the
engine's read of the readable part, or stays as-is, instead of becoming a plausible-looking
English sentence the source cannot support.

## What it does

- **Dashboard** — upload page images or a `.cbz`/`.zip` archive, run a translation job, watch it
  page by page with a live stage indicator, stop/pause/resume, and see cost from real token usage.
- **Viewer** — side-by-side original vs translated page, download pages or the whole job.
- **Output** — output folder, re-assembled `.cbz`, or mirror; **dry run** reads the page without
  spending on translation; one page or a whole job can be **re-rendered** after a pipeline change.
- **Second pass** — text the engine's detector missed is found by our own detector and lettered
  (skipped automatically when the host is short on memory). It only ever *adds*: a region the
  engine read is never touched.
- **Settings** — models and keys; Advanced: engine on/off, engine URL, detector, detection
  resolution, box threshold, unclip ratio, render direction, inpainter + resolution, mask
  dilation, lettering (ours or the engine's), source-language presets, second pass, target
  language, font, output mode, GPU device (local auto-detect / remote worker / CPU), timeouts.
- **Logs** — six surfaces, all readable and downloadable from the Logs tab (see below).

## Decisions (locked)

- Input: **web upload** (no watch-dir / Suwayomi integration in v1).
- Automation: **fully automatic, one-shot** — no review step in v1.
- Output: user-selectable; **default = leave-as-is** (no auto re-assembly); CBZ re-assembly is opt-in.
- Translation: **cloud-only via DeepSeek** (`deepseek-v4-flash` default), runtime-configurable via `MANGAFILL_MODEL`/`MANGAFILL_BASE_URL`. No local-GPU translation.
- Vision GPU: runs locally (CUDA/ROCm) when the host has one, or offloads to a remote GPU worker (NVIDIA / AMD / CPU variants — see the [GPU worker setup guide](gpu-worker/SETUP_GUIDE.md)); reserved for inpaint/detect, never translation.

## Run

**Build and run:**

```bash
cp .env.example .env          # add your model API key (MANGA_FILL_DEFAULT_API_KEY)
docker compose up -d --build
# dashboard at http://localhost:8788
```

## Engine

The pipeline itself (detect → OCR → inpaint → lettering) runs in an **engine**: the upstream
[`manga-image-translator`](https://github.com/zyddnys/manga-image-translator) service (GPL-3.0,
pinned commit). This app is the shell around it — jobs, queue, viewer, fonts, CBZ/PDF, logs.

```bash
cd engine
docker compose --profile cpu up -d --build     # CPU (used for all testing)
docker compose --profile nvidia up -d --build  # NVIDIA host (needs the nvidia runtime)
docker compose --profile rocm   up -d --build  # AMD host (needs /dev/kfd, /dev/dri)
```

Then point the app at it in **Settings → Engine** (`http://<host>:8000`) and flip the switch.
Leave the URL empty and the app falls back to its own built-in pipeline, exactly as before.

- **One image per accelerator type** (house rule): the same `engine/Dockerfile` builds a CPU,
  CUDA or ROCm variant from the `VARIANT` build arg — so the engine containers are portable
  between hosts of the same kind.
- **Models are not baked in.** They are fetched on first use into `engine/models/` (a volume),
  so the image stays a few GB and one cache serves every later job.
- **Translator keys come from the engine container's environment** (`DEEPSEEK_API_KEY`,
  `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, …) — upstream reads them from env, which
  is why this app's own model list keeps working untouched.
- **Advanced tab** (Settings): detector, detection resolution, render direction, inpainter,
  inpainting resolution and the three fine knobs (box threshold, unclip ratio, mask dilation).
  Every default is the fixed value this app used before the engine existed, so nothing changes
  until you change it.
- **Source-language presets** (also under Advanced, on by default). A measured sweep of six
  configurations over 10 pages of each of Japanese, Korean and Chinese (180 renders, scored by how
  much source text survives on the finished page) found one large, reproducible win: Korean leaves
  **44% less** untranslated text with the detector at 2560 and box threshold 0.6, at roughly
  9.8 → 14.7 s per page on CPU. Korean jobs now use those values; Japanese and Chinese keep the
  defaults (their differences were small, or inside the Chinese pages' watermark noise — about 60%
  of a Chinese page's "leftover" text is a site watermark, not dialogue). The applied preset is
  named in each job's log line, and switching this control **off** hands full control back to the
  Advanced values. Jobs left on source-language **auto** are detected once, in a separate child
  process, so the right preset applies without risking the web process's memory.
- **Output language**: the engine's full list, English by default.

## Logging

Verbose by default, on **six surfaces**, all readable from the Logs tab (and downloadable):

| File | What lands in it |
|---|---|
| `mangafill.log` | everything the app emits (DEBUG by default) |
| `access.log` | every HTTP request: method, path, status, duration |
| `lifecycle.log` | state changes: job/page transitions, engine submissions, dry runs |
| `engine.log` | the engine container's **own** stdout/stderr (it tees them into a shared volume) |
| `engine-calls.log` | one line per engine request we make: page, status, bytes, ms |
| `errors.log` | WARNING and above from anywhere — one file holds every failure |

Daily rotation, 45-day retention, timestamps in the configured IANA timezone
(`Australia/Sydney` by default — no hardcoded DST offsets), and secrets are redacted on the way
into every file.

## License

**GNU Affero General Public License v3.0** — see [LICENSE](LICENSE).

Manga Fill was MIT up to v0.27.x. From **v0.28.0** the project is distributed under
AGPL-3.0 because it vendors GPL-3.0 code from
[manga-image-translator](https://github.com/zyddnys/manga-image-translator): the per-block
balloon geometry that keeps two touching balloons from being read as one region
(`app/vendor/manga_image_translator/ballon_extractor.py`, pinned commit `95227a2b`).
AGPL-3.0 §13 permits combining a covered work with GPL-3.0 code; the vendored files stay
GPL-3.0 and keep their notices.

Full attribution, the licence texts and what the AGPL-3.0 network clause means for anyone
running this as a service: **[THIRD_PARTY.md](THIRD_PARTY.md)** and
[LICENSES/](LICENSES/). Everything else here — the pipeline, the jobs/UI layer, the QA
harness — is Manga Fill's own code.

## Credits

Manga Fill stands on other people's work, and this is what it is:

- **[manga-image-translator](https://github.com/zyddnys/manga-image-translator)** (GPL-3.0,
  © its contributors) — the detection + OCR + translation engine, the per-block balloon
  geometry vendored in `app/vendor/`, and the source of the **translation prompt** this app
  sends (its rule that unreadable text is output as-is is what keeps a clipped line from being
  answered with invented English). Pinned commit and the vendored file: `THIRD_PARTY.md`.
- **[manga-ocr](https://github.com/kha-white/manga-ocr)** (Apache-2.0) — Japanese OCR.
- **[PaddleOCR / PP-OCRv5](https://github.com/PaddlePaddle/PaddleOCR)** (Apache-2.0) — Korean
  and Chinese OCR.
- **[LaMa](https://github.com/advimman/lama)** via `simple-lama-inpainting` (Apache-2.0) —
  erasing the source text before lettering.
- **[Comic Shanns](https://github.com/shannpersand/comic-shanns)** (MIT, © 2020 Shannon Miwa)
  and **Comic Neue**, **Bangers**, **Patrick Hand**, **Gloria Hallelujah** (SIL OFL 1.1) —
  lettering faces, shipped here with their licence texts in `fonts/`.
- **Anime Ace** (Blambot) — the plain-English display face this app defaults to. Its licence
  forbids redistribution, so it is **not** in this repository; the Dockerfile fetches it at
  build time and lettering falls back to DejaVu Sans Bold if that fetch fails.
- **`ogkalu/comic-text-and-bubble-detector`** — the balloon detector whose weights are fetched
  at runtime (HuggingFace), as are all other model weights; no weights are committed here.
