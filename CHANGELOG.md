# Changelog

All notable changes to Manga Fill are documented here (Keep a Changelog format).

## [Unreleased]

### Changed
- **Release hygiene** — publishing now runs an automated scan of the tree and of every added
  line for internal infrastructure references (service names, addresses, host paths, operator
  details, credentials), and the publish path refuses to push when it finds any.

## [0.30.7] - 2026-09-25

### Fixed
- **The engine's own language answer is now taken per PAGE — and a page it read can no longer
  be silently blanked.** Found on the first engine-mode job in production (job 4, 2026-09-25):
  a two-page job whose first page is Japanese left `lang="ja"` set for the whole job, so when
  the SECOND page (Korean, and a colour page) came back from the engine as `source=ko`, the
  guard `lang not in ("ja","ko","zh")` refused the engine's answer. The render then took the
  Japanese path, its colour-page skip fired, and that page came back with **0 blocks and no log
  line at all** while the job reported `done (2/2, 0 failed)`. Two changes:
  - the engine's answer is adopted whenever the source language was NOT chosen by the user (a
    Settings value still wins), and the change is logged per page
    (`page N: engine reads source=ko (was ja) — using the engine's answer`);
  - the JP colour-page skip and the blank-page skip now SAY so in the log, and the colour skip
    does not apply to a page the hybrid supplied engine regions for — the engine read text
    there, so the page has dialogue whatever the colour test thinks.
  A page that comes back untouched is now always a logged decision, never a silent one.

## [0.30.6] - 2026-09-25

### Changed
- **The translation prompt is upstream's own now.** In engine mode the engine translates every
  line it reads with upstream's prompt; the only lines that reached OUR prompt were the ones
  the second pass recovers — i.e. exactly the degraded reads (a line clipped by a panel edge, a
  tail mangled by OCR) — and ours answered those with invented English: on the Korean page 001
  the clipped fragment `1이르다리고이느` was lettered as "One, get up and move!", and because
  the merged read replaced the engine's region it also displaced upstream's own correct
  "South Korea sales scale" for that plate.
  Upstream's prompt already answers that case in one clause — *"If it's already in {to_lang} or
  looks like gibberish you have to output it as it is instead"* — with its method's *"leave
  ambiguous elements as they are without interpretation"* and *"do not add pronouns that do not
  exist in the original text"*. So we use their prompt
  (`manga_translator/translators/config_gpt.py` @ `95227a2b`), adapted only to our `N.`
  numbering and our two render-side needs (fit the balloon, keep a name off its own dialogue);
  the Korean register note stays, and their few-shot example is sent as they send it. Recovered
  lines now read in the same voice as the lines the engine translated itself.
- Nothing defensive was added: no markers, no abstention rules, no anti-invention scaffolding
  of ours. The behaviour comes from upstream's prompt, which is what the request was.
- **The second pass ADDS ONLY — it can no longer touch a region the engine read.** A recovered
  box that touches an engine region, whether it sits inside it or extends past it, is left
  alone: the engine read that text and translated it, and our read of the same area is the
  degraded one (that is why upstream's detector stopped where it did). Measured 2026-09-25: the
  merged form answered the Korean plate's clipped line (`대한민국매출규모 1이르다리고이느`) with
  *"South Korea sales scale 1ireudarigoineu"* — romanised garbage, lettered over the plate in
  place of the engine's own clean *"South Korea sales scale"*. Only a box that touches NO engine
  region is added, and if even that read cannot be translated it is not drawn. Every failure
  this pass ever shipped came from the merge (v0.30.1 lost a plate's English, v0.30.2
  duplicated the same text, v0.30.5 lettered garbage); there is no drop path and no second
  translation of someone else's line any more.

### Measured (whole corpus, ja 4,307 / ko 274 / zh 241 boxes, one blind judge, 9/9 calibration)
OLD = the prompt shipped before this release · NEW = the shipped path. Judged pairs: 294
degraded ("suspect") and 516 clean ("control").

| | SUPPORTED | INVENTED | GAP | lines handed back as the source |
|---|---|---|---|---|
| SUSPECT OLD | 62.2 / 59.2 / 94.5 % (ko/ja/zh) | 7.6 / 5.0 / 5.5 % | 16.0 / 31.7 / 0 % | 0 / 0 / 1.8 % |
| SUSPECT NEW | 70.6 / 61.7 / 94.5 % | 3.4 / 1.7 / 1.8 % | 17.6 / 31.7 / 1.8 % | 1.7 / 5.0 / 3.6 % |
| CONTROL OLD | 89.3 / 82.4 / 94.8 % | 2.7 / 3.2 / 0 % | 0.9 / 4.0 / 0.6 % | 0 / 0 / 0.6 % |
| CONTROL NEW | 92.0 / 85.2 / 97.4 % | 2.7 / 1.2 / 0 % | 0.9 / 4.8 / 0.6 % | 0 / 0.4 / 0.6 % |

Pooled: degraded-source INVENTED **6.1% -> 2.4%** (18 -> 7 of 294), clean-source SUPPORTED
**87.6% -> 90.3%** (452 -> 466 of 516), clean-source INVENTED 2.1% -> 1.2%. The cost is the
honest one upstream's own rule implies: 1.7-5% of degraded lines come back as the source and
are left untranslated (the artwork is kept rather than answered with a guess), with GAP flat on
ja/zh and +1.6 pt on ko.
Two defects in this same change were caught by measurement, not by reading it: formatting the
SOURCE language into upstream's `{to_lang}` slot (their "already in {to_lang}" clause then made
the model hand back 15.1% of degraded Korean reads and 8.3% of clean Japanese lines as-is), and
a measurement script that assembled its own messages — every "NEW" number above is now produced
by the app's own `_messages`, so it is the shipped behaviour by construction.

### Fixed
- A line the model hands back as the source is no longer chased by the per-line retry (it is a
  final answer, not a dropped line), and a stray ASCII digit that survives ASCII-stripping
  (`1이르다리고이느` -> `1`) is never lettered as if it were a translation.

### Tests
- `tests/unit/test_upstream_prompt_and_fallback.py` — the prompts carry upstream's rules and no
  scaffolding of ours remains; `{to_lang}` is the TARGET and the source is named separately; the
  request carries upstream's example; an echoed source is recognised and not retried while an
  omitted line still is; a recovered box that touches an engine region changes nothing, a
  standalone miss is added when translatable and never lettered when it is not.

## [0.30.5] - 2026-09-25

### Fixed
- **The hybrid second pass works now — and the defect was never in the pass.** `_merge_blocks_per_bubble()`
  rebuilt a merged block from its joined **source text alone**, dropping `translation`. That is
  harmless in the OCR path (those blocks are translated *after* the merge), but engine (hybrid)
  blocks arrive **already translated** — so as soon as two blocks resolved to the same bubble (the
  engine's lower-plate region plus the second pass's recovered box for that same plate) the merged
  block carried no translation at all: the region loop skipped it for having nothing to draw, so
  nothing was erased and nothing was lettered. The page came back with the source language still on
  it while the job read `2 blocks · 1 translated`. That single defect explains all three failed
  second-pass forms (v0.30.1–v0.30.3); `second_pass.py` was behaving correctly the whole time.

  The merge now carries the members' translations through, joined in the same reading order as the
  text, so an engine-translated block keeps its English after merging.

### Changed
- The bubble merge is **logged at INFO through the app logger** instead of `print()`, so it appears
  in the webui log viewer like every other page-changing decision. Its invisibility on stdout is
  why this went unnoticed across three releases.
- `engine_second_pass` is **on** by default again (v0.30.4 had shipped it off while the cause was
  unknown). It recovers text upstream's detector never reported, e.g. the tail of
  `대한민국매출규모 1위를 다리고 이는…`.

### Tests
- `tests/unit/test_render_lookahead_ocr.py::test_merge_keeps_the_engine_translations` and
  `::test_merge_of_untranslated_blocks_still_has_no_translation` — the fix, and the OCR path
  unchanged.

## [0.30.1] - 2026-09-25

### Added
- **The second pass: text the engine misses is now found, translated and lettered.**
  Upstream's API only returns regions it *translated*, so anything its detector skipped was
  never erased and stayed visible under our English — measured on the Korean test page, where
  it read `대한민국매출규모` and stopped, leaving `1위를 다리고 이는…` on the white plate under
  "SOUTH KOREA SALES SCALE". The same class shows up as surviving vertical kana on Japanese
  pages. With the second pass on (Settings → Advanced → **Second pass**, default on), this
  app's own detector (manga-ocr for ja, PaddleOCR for ko/zh) finds the text the engine did not
  report, anything already covered by an engine region is dropped so nothing is lettered
  twice, and the survivors are translated with our LLM — a few blocks per page.
  It is **conservative by construction**: skipped automatically when the host is low on memory
  (our OCR is what OOM-killed this box before), never called on a dry run, and any failure
  returns nothing at all so the page is lettered from the engine's regions exactly as before.
  The outcome — including "0 missed" or the reason it did not run — is written to the job's
  `lifecycle` log line.

## [0.30.0] - 2026-09-24

### Changed
- **The engine now reads the page and this app draws it (the hybrid).** v0.29 handed the
  *whole* pipeline — including lettering — to upstream, and the output showed it: text sat
  small inside the detected box instead of filling the balloon, and escaped the frame. Our own
  typesetter is the part that was already finished (fitted to the balloon's outline, filling
  it), so it is back doing the drawing, while the engine keeps the part it is better at:
  detecting and reading the text (measured −44% source text left on Korean pages).
  Mechanics: `engine.translate_page_regions()` calls upstream's `/translate/with-form/json`
  (which returns each block's box, source text and translation and renders nothing), and
  `render_translated_page(..., engine_regions=...)` letters those regions with the normal
  pipeline — our detector still supplies bubble geometry, because the balloon-fit path only
  runs when bubble regions are present. No LLM is called by this app for those pages: the
  engine did the translating.
- **Settings → Advanced → Lettering** switches between *our typesetter* (default) and the
  engine's own renderer, so the two can be compared with one click. The choice is named in
  every job's log line.

### Fixed
- Balloons are no longer lettered as plain rectangles in engine mode — that fallback (no
  bubble regions) is what produced small, box-centred, boundary-escaping text.

## [0.29.1] - 2026-09-24

### Added
- **Per-source-language engine presets**, measured rather than guessed. A six-arm sweep
  (10 pages × 3 languages per arm, 180 renders) scored by leftover source characters per page
  found one large, reproducible win: **Korean improves 44%** (5.0 → 2.8 chars/page) with the
  detector at 2560 and box threshold 0.6, which three independent arms agree on. Korean jobs
  now use those values; **Japanese and Chinese keep the long-standing defaults** because their
  differences are small (ja) or inside the site-watermark noise (zh — ~60% of a Chinese page's
  "leftover text" is the 腾讯动漫 watermark, which no setting can translate).
  Cost on CPU: Korean ~9.8 → ~14.7 s per page. Settings → **Advanced → Source-language
  presets** switches them off for full manual control; the applied preset is named in each
  job's log line.
- **Source-language probe for auto jobs** (`app/pipeline/lang_probe.py`). The presets are keyed
  on the source language and upstream never reports the language it detects, so a job left on
  "auto" now detects it once — in a **child process**, because doing it inside the web process
  is what OOM-killed it at 4.05 GB while the engine's models were resident. It refuses to run
  below **2.5 GB** free memory — a threshold measured, not guessed: with the engine's models
  resident (2.70 GB) the probe's child peaked at ~1.9 GB and host free memory bottomed out at
  1295 MB with nothing killed. Any failure (OOM included) just falls back to the engine's
  defaults, with the reason in the log.

## [0.29.0] - 2026-09-23

### Changed
- **The pipeline is now an ENGINE; this app is the shell.** Upstream
  `manga-image-translator` (GPL-3.0, pinned commit `95227a2b`) runs detect → OCR → inpaint →
  lettering as a service, and Manga Fill keeps what it is good at: the job library, queue,
  viewer, fonts, CBZ/PDF reassembly, dry run, cost tally and the Logs page.
  `engine/Dockerfile` builds a **variant per accelerator type** (`VARIANT=cpu|nvidia|rocm`),
  `engine/docker-compose.yml` exposes one profile each; models are fetched on first use into a
  volume rather than baked into the image (image 1.41 GB, models 511 MB so far). Point the app
  at it in **Settings → Engine** and flip the switch; leave the URL empty and the built-in
  pipeline runs exactly as before, which is the shipped default.
- **Logging rebuilt onto six surfaces**, all readable from the Logs tab with a picker and a
  download: `mangafill`, `access`, `lifecycle`, `engine` (the engine container's own
  stdout/stderr, teed through a shared volume), `engine-calls` (one line per engine request,
  including every pipeline stage) and `errors` (WARNING+ from everywhere). Daily rotation,
  45-day retention, IANA-timezone timestamps, secrets redacted on the way in. `GET
  /api/logs?lines=N` keeps its old behaviour.
- **Settings → Advanced**: detector, detection resolution, inpainting resolution, render
  direction, inpainter and the three fine knobs (box threshold, unclip ratio, mask dilation).
  Every default is the fixed value this app already used, so nothing changes until one is
  touched.
- **Output language** picker: the engine's 26 languages, English default.
- **Upload** accepts pasted images, like upstream's UI.
- **Font**: Comic Shanns (MIT, © 2020 Shannon Miwa) added to the list and shipped in-repo with
  its licence — the same face the engine letters English with. Upstream's other fonts are
  proprietary and were not taken.

### Fixed
- **The engine client uses `POST /translate/with-form/image/stream`.** Upstream's plain
  `/translate/with-form/image` answers 500 (`'utf-8' codec can't decode byte 0x80`) while its
  dispatcher reassembles the child instance's binary reply, and `/stream/web` returns a 69-byte
  1×1 placeholder meant for their own React UI. The stream endpoint returns the finished page
  and reports each stage, which also gives us per-page stage logging.
- **Engine mode no longer runs our own source-language detection.** That step loaded PaddleOCR
  while the CPU engine was resident and the host OOM-killed the app mid-job on zh
  (`uvicorn anon-rss 4.0 GB` on a 7.9 GB box — see `dmesg`). The engine detects the source
  language itself.
- **No `source_lang` in the engine payload**: upstream's `Config` has no such field (their own
  front sends only `{translator, target_lang}`), so a wrong guess could only mislead a reader
  of the logs.
- **Redaction no longer eats ordinary words**: the `sk-` rule matched inside
  "ma**sk-generation**" and replaced it; it now requires a word boundary and 20+ key
  characters.
- **Advanced size selects rendered "undefined"**: the select filler didn't handle the engine's
  bare-number lists (1024/1536/2048/2560).

### Parked
- The 0.28.0 local-balloon geometry port stays in the tree with `balloon_local.ENABLED = False`
  (measured neutral). Engine mode supersedes it; the code and its GPL-3.0 provenance stay.

## [0.28.0] - 2026-09-23

### Changed
- **LICENCE: MIT → AGPL-3.0, and the first GPL-3.0 code in the tree.** The project now
  vendors `manga-image-translator`'s `ballon_extractor.py` (GPL-3.0, pinned commit
  `95227a2b`, body SHA-256 recorded in the file header) under
  `app/vendor/manga_image_translator/`, with the GPL text in `LICENSES/GPL-3.0.txt` and the
  attribution — plus what AGPL-3.0 §13 means for anyone running this as a service — in
  `THIRD_PARTY.md`. AGPL §13 is what makes combining GPL-3.0 code with this project legal;
  the vendored file stays GPL-3.0.

### Fixed
- **A block's lettering region no longer spans TWO balloons.** The region resolver worked
  page-wide (detector bubble blob, white flood, gap-tolerant flood), so two balloons whose
  outlines touch — or whose outline our own erase removed — came back as ONE interior and the
  English was fitted across both outlines. The v0.27.41 whole-library audit put this class at
  **591 regions shared by more than one block, covering 1,329 of 2,606 blocks (48.8% flagged
  `merged`, 176 pages)** — the largest remaining defect class, and one that three releases of
  in-place clipping had not moved.
  The fix takes each block's balloon from a **local window on the original page** (the
  vendored extractor: Canny → contours → flood from the window centre, so a neighbouring
  outline inside the window acts as a wall instead of merging with ours) and hands the
  resulting mask to the fitter as the lettering profile directly.
  Measured before porting, on **all 591 merged groups / 1,329 blocks** of the v0.27.41 job-3
  render: the local mask covers the block's **own** text box **96.9%** (median 0.986) and a
  **sibling's 3.0%** (median 0.000); **89.6%** of sibling pairs are fully separated and 0.1%
  still merged, against 100% "merged" by construction for the shared region. Window width is
  what decides it — 3.4% sibling coverage at 1.5x, 15% at 2.5x, 31% at 4.0x — so the window
  follows upstream's own rule (aspect-based, capped at 3x, **shrunk** for any neighbour whose
  enlarged box would meet ours: 100% separation but 16.4% refusals) with a fixed-1.5x retry
  for the refusals (93.1% separated, 1 refusal).
  Scope is deliberately narrow: the local extraction is consulted **only** where the resolved
  region already swallows a sibling's box, it is **refused** if the extraction still covers a
  sibling (so an unambiguous block can never be made worse), it can only ever TIGHTEN a region,
  and caption strips (class E) and drawn SFX are excluded.
- Both language paths are covered — the JA path (bubble → container → gap-tolerant flood) and
  the KO/ZH path (bubble → colour flood → caption strip) — because the merged class is not a
  Japanese-only defect: it is **23.4% of the zh manhua job and 2.6% of the ko webtoon job**
  (see the placement-audit baselines).

  **Verification status is recorded honestly in the release notes of the deploy session**: the
  numbers above are the pre-port measurement on stored data; the whole-render, three-language
  audit and the browser/vision pass are the gate for calling it fixed.

## [0.27.41] - 2026-09-22

### Fixed
- **A block's lettering region is clipped off its SIBLING blocks' own text boxes.** Two
  touching or overlapping balloons flood as ONE interior, so the fitter was handed a union
  box spanning both and the English was drawn straight across the shared outline — job-3 p10
  had a 427x445 region shared by three blocks and a 313x355 one shared by two (`CO! CO...`
  lettered at 23px over artwork belonging to the neighbouring balloon; `How far can you go?`
  using 22% of the width it was handed). The SOURCE TEXT'S OWN BOX is the anchor: a block may
  use the balloon around it, never the neighbour's half — each side of the region is pulled
  in to the first sibling box that overshoots it, never inside the block's own box
  (`render._clip_region_off_siblings`). Reach on job 3: **899 of 3,515 region resolutions
  (25.6%) clipped**, median region width **200 -> 153px**; on the reported page-9/10 pair the
  ink crossing the two outlines went **686px -> 6px**, and whole-job ink landing outside its
  balloon fell 895 -> 819 blocks (36.5% -> 33.4%).
- **A trailing ellipsis is now a legal line break** (`typeset._token_breaks`). `"normal"...`
  is ONE token — the quotes and the ellipsis ride along with the word — and on job-3 p10 it
  was the only thing capping that balloon: the token measured 120px at 17px against a 116px
  width profile, so every size above 16 was rejected and the balloon kept a small stacked
  column where the source filled it. Breaking in front of the ellipsis run is standard comic
  typography and costs nothing visually; the block reached **19px (+19%)**, ink 107x123 ->
  128x136. Whole job: **1,334 blocks larger, 208 smaller, median +1px**, and every large jump
  is ellipsis-ending dialogue (`...But...` 15 -> 28px, `So sour!` 19 -> 32px). A bare word is
  still never cut — the hyphen-only rule and `test_wrap_word_wider_than_box_does_not_hang`
  pin that.
- **A block the configured face cannot letter legibly now gets a NARROWER bundled face.**
  Anime Ace is the widest face in the catalog, and on tight boxes that costs the size: on
  job-3's character sheet the 140x26 stat cell letters `Defense Technique: B+` at **8px**,
  where Comic Neue reaches **12px** and Patrick Hand 11px in the SAME box. Measured across
  30 under-legible blocks on 7 pages (2026-09-22): the configured face reached the 14px
  floor on **0** of them, Comic Neue on **21** (mean +5px), Patrick Hand on 20, Bangers on
  23 (all-caps display — rejected for mixed-case dialogue). `typeset._LEGIBLE_FLOOR` (14px)
  is the trigger: only a block already under it is re-fitted, in the same box, and the
  narrower face is used only when it is STRICTLY bigger. Nothing moves, nothing shrinks,
  and the configured face wins wherever it is already legible (byte-identical output —
  pinned by `tests/unit/test_legibility_fallback.py`). Drawn SFX keeps the configured
  display face (it is art, not prose); `$MANGA_FILL_FALLBACK_FONT` overrides the choice.
  The fitlog now records `face` and `fallback` per block, and the per-page FITLOG line
  carries `fallback=N`, so the switch is auditable rather than silent.

### Added
- **Deploy host, not a released file:** `scripts/rollback.sh` — one-command rollback (step
  back one release, `--list` the tags, or pass a tag): fetch tags, verify a clean tree,
  snapshot the running image, `git reset --hard <tag>`, `docker compose up -d --build`, then
  WAIT for HTTP 200 and report the served version + tag. It lives in `scripts/`, which is
  gitignored, so a rollback cannot remove the rollback tool itself. Verified end-to-end on
  2026-09-22 (the first version curled once, too early, and reported a healthy rollback as
  unhealthy — it now polls for readiness). It does NOT touch rendered pages: re-render a
  job to regenerate its pages with the rolled-back build.

### Notes
- Every number above is measured, not eyeballed: `mfqa/placement_audit.py` over all 193
  pages / 2,606 lettered blocks of job 3, run before and after on identical input
  (`mfqa/V02741_REPORT.md`, `mfqa/GATE_v02741_REPORT.md`). Combined effect of the three
  fixes: ink outside the balloon **895 -> 798 blocks (36.5% -> 32.5%)**, blocks the
  configured face could not letter at 14px **328 -> 276 (13.4% -> 11.2%)**, median width
  fill **0.71**. Honest limit: 1 block in 3 still places some ink outside its own balloon,
  mostly where two outlines genuinely overlap or the balloon is smaller than the English
  needs — that residual is the case for the engine change.
- Verified before this release: **363/363 unit tests** green in the deployed image, and a
  full 193-page render + audit on the test instance.
- NOT in this release: the terse-label translation prompt (`translate._LABEL_RULE`,
  v0.27.42). It changes translated TEXT, so it is held out of a placement release and
  validated on its own — `tests/unit/test_label_prompt.py` ships with it, not here.

## [0.27.40] - 2026-09-22

### Fixed
- **Narration drawn OVER artwork can now widen across the art (class E).** `_caption_region`
  grew a caption strip only into un-inked, unclaimed space, which is right for a caption
  beside artwork and wrong for narration inked ON artwork. Job-3 page 10: a 54x346 column of
  vertical narration lies across the character's hair with ink on BOTH sides, so the strip
  could not grow, the English was lettered into 54px, and the fit collapsed to **8px** — seven
  8px lines beside dialogue lettered at 22px on the same page. The source text had the same
  problem and solved it by drawing wider text over the art; our lettering carries its own
  outline and stays legible there. Measured after: the column's region 54px -> **109px** and the
  font rose to normal size (verified in pixels).

  Three things had to be right. (1) Artwork must not veto the width, but a **panel rule** must:
  both are "ink", and shape is what separates them — a rule is a thin run of columns inked
  CONTIGUOUSLY through the strip's height, while a character's hair with its highlight streak
  is interrupted (the coverage-based first attempt classified the hair as a wall and killed the
  fix for the very block it was written for). (2) The growth must be **asymmetric**: p10's
  column sits 4px from its panel's left border with open art to the right, and the symmetric
  room function capped the growth at those 4px — the first version did nothing on its own test
  case. (3) A 50x75 single-word box is a column too; the 1.5 aspect bar missed it by a pixel
  ("Flinch", 9px), so the threshold is now 1.3 in both call sites.
- **Readability on job-3 pages 9-12: blocks lettered under 14px went 7 -> 4**, and the three
  that were fixed are exactly the class this release targets: the 8px narration column
  ("I nearly died a few times...", now ~3x the size), an 8px "Hmm..." and "Me and age"
  (8px -> 13px). The four that REMAIN are not this class — an 8px 87x47 dialogue box, a 9px
  one-word SFX ("Flinch"), an 11px "Yes." in a 43x30 box and a 12px "Hmph." — every one of
  them a SHORT box with English that needs more room than the source took, which is the next
  fix (text-aware widening for short-wide boxes, below). Measured from the fitter's own trace
  (`MF_FITLOG=1`), not from the changed-pixel proxy that reported a phantom 24px -> 13px on a
  pixel-identical page.

### Quantified for the next release
- Across 1,054 measured blocks, **179 (17%) are lettered under 14px**: 29 are stat-table lines
  ("Defense Technique: B+", "Mental Strength: B-"), the other 150 are dialogue/free text, and
  **141 of those 179 had NO balloon at all** (they took the rectangular fallback). The English
  needs ~2.5x the room the Japanese did, so a short box (87x47 with 35 characters, a 43x30
  "Yes.") can only be lettered tiny. The fix is to size the widening from the ENGLISH it must
  hold rather than from the source box's shape — the same machinery this release added, applied
  with a text-length test instead of an aspect test. 109 of those boxes are short-WIDE (the
  1.3 aspect bar cannot see them) and 70 are tall.
- **A bare-punctuation block is no longer lettered.** A lone `。` was a block, translated to
  ".", and lettered as a one-glyph caption (job-3 page 9, box 41x21). `_punctuation_only()` now
  leaves such boxes alone — the mark is already on the page.
- **The crossing audit no longer measures against a degenerate envelope.** Job-1 page_009's
  "enclosure" was the source's glyph BLOBS plus a decorative arc — 19,205px of fragmented
  shape — and 3,697px of lettering was counted "outside" it, while the falsifiability control
  passed (the source glyphs really are part of that mask). `_envelope_is_usable()` requires the
  mask's rows to be mostly one contiguous run and a >= 0.3 fill of its own box; failure means
  UNMEASURABLE, never "clean". Job 1: 3,729px -> **32px**, and the library total 51,744px ->
  **37,331px** with the same renders — 14,400px of that earlier number was this artifact.

### Investigated and deliberately NOT done
- **The "hallucinated English" guard (j3 p4) was dry-run over all 2,606 job-3 blocks: 4
  candidates, ALL FOUR correct translations** ("You really don't have to." from どうしてもいいのよ,
  "...Or-di-nary." from 「ふっう」, "Ohh! Beren-san too" from へー！ベレンさんも), zero hallucinations
  caught — and the ink measure it relies on is unreliable on thin/small glyphs. Not enabled
  (0 true positives / 4 false positives). The original concern needs a direct look at that page.

### Notes
- This release changes REGION RESOLUTION, which the replay harness cannot gate (it replays the
  typeset stage only). It is gated by a full library re-render plus `crossing_audit.py`, and
  the numbers above are from that run.

## [0.27.39] - 2026-09-21

### Fixed
- **The width profile is indexed from the box it is handed over with.** `_region_avail` places
  its box around the balloon's AXIS (the width-weighted centroid), but returned the per-row
  widths indexed from the interior's bbox top. Where those disagree, the fitter is told that
  rows ABOVE the balloon are as wide as the balloon's waist. MEASURED, job-3 page 45
  (`Though I don't know if I'll come again next time...`): the box's top sat 15px above the
  interior's first row, the profile still reported 116px of width up there, a line was placed
  in those rows, and it was drawn across the top arc. Isolated against v0.27.38: **p45 outside
  ink 527px -> 21px**, 89 blocks improved vs 37 worsened, total outside ink 223,142px ->
  215,424px (-3.5%). The rows a box reaches beyond the interior now carry width 0, which is
  the whole point: no width was ever measured there.

  Credit where it is due: this was first attributed to the enclosure work and then to the
  clip. Both were wrong. v0.27.38 vs v0.27.38 + the enclosure change is **0 pixels** on that
  page — the cause was the profile/box alignment, in code that shipped in v0.27.37. The
  invariant now has its own test, on the captured page-45 and page-85 geometry.

  Note on p45's enclosure: its overhang is 53px left / 52px right — symmetric, i.e. one broad
  oval, so the two-balloon guard correctly does not fire there. Not every wide balloon is a
  union.

### Changed
- `crossing_audit.py` reports TWO crossing numbers per book: outside the enclosure (stable,
  does not move when the renderer's bounding rules change) and outside the block's own box
  window (strict, catches wall crossings, over-reports when a balloon is genuinely wider than
  its source text). A single number can be moved by an envelope-definition change rather than
  by the render — measured: job-1 page_009 rose 209px -> 3880px between releases with the
  renders PIXEL-IDENTICAL.

## [0.27.38] - 2026-09-21

### Fixed
- **Lettering can no longer be fitted to TWO balloons at once.** Job-3 page 85 (the page the
  user caught from a screenshot after v0.27.37 had been reported fixed): `Even though I took
  the alcohol breakdown medicine Hazal gave me earlier-` was sized to **274px** of "available"
  width for a **156px** block, and its first line was drawn across the wall into the
  neighbouring balloon. The two balloons touch, and where they meet their light interiors are
  ONE connected region — the distance transform is a single component at every threshold up to
  60px, so there is no neck to cut, and the flood on the INPAINTED page merges the same way
  (274px). The enclosure is now bounded by the block's OWN box — the extent the source text
  occupied — whenever the enclosure hangs off ONE side of that box by more than 0.45 of its
  width. The bound is an INTERSECTION, never a shape of its own: the per-row extents stay the
  enclosure's (narrow where the oval narrows) and only the width is capped.

  The one-sided part is load-bearing. A broad oval whose source text is a narrow column also
  exceeds 1.5x the box, on BOTH sides; bounding those left 68 blocks worse against 260
  improved in the first volume gate. A "cut the neck" variant (erode the enclosure until the
  parts separate, keep the focus's part) was built, measured and REMOVED: growing the eroded
  core back returns a lozenge with hard edges — a rectangular profile that overstates the
  oval's narrow rows, so the fit sized UP and the placement believed it fitted (job-3 p45,
  outside ink 21 -> 527px).

  GATED in isolation against what is on prod (v0.27.37 -> v0.27.37 + this change, 344 pages,
  3021 blocks): **200 blocks improved vs 36 worsened; ink outside the balloon 265,124px ->
  223,142px (-15.8%)**. 1787 blocks are reported UNMEASURABLE (no closed balloon — captions,
  SFX, art) and never counted as clean.

### Added
- **`crossing_audit.py` — the pre-release gate.** It audits a live job directory (original +
  rendered page + the stored boxes) and reports, per book, how much lettering ink falls
  outside the balloon the ORIGINAL encloses, with the worst offenders listed. It carries the
  project's falsifiability control (if the ORIGINAL's own glyphs are not inside the envelope
  the block is excluded) and the glyph-shaped-component filter (the inpainter's smears and its
  repaint of the erased outline are not lettering — counting them flagged p164 at 1217px when
  its lettering is visibly inside). Baseline on the shipped library: **61,176px** across the
  three books. The number may not rise between releases.
- **`size_impact.py`** — chosen font size per block, old vs new, read from the fitter's own
  trace (`MF_FITLOG=1`). The replay diff's "glyph height" line measures the CHANGED INK BLOBS,
  which for a block that barely moved is a sliver, not a glyph (it reported 24px -> 13px on job-3
  p130 where both renders are pixel-identical — both were 24px).

### Known, measured, not fixed here
- Job-3 p45 (`Though I don't know if I'll come again next time...`) is lettered LARGER than in
  v0.27.36 and its first line crosses the top arc. Isolated: v0.27.37 vs v0.27.37 + the above
  is **0 pixels** on that page — it comes from the v0.27.37 PLACEMENT change (`_placement_top`
  sliding a block instead of shrinking it), which is already on prod. Reverting or fixing the
  placement is the next item, gated the same way.

## [0.27.37] - 2026-09-21

### Fixed
- **Lettering can no longer be drawn outside a balloon whose outline our own erase removed.**
  The fitter measures the balloon on the page it is handed, which is the INPAINTED one — and
  the erase mask runs over the strokes inside a block's box, so it takes the balloon's outline
  with it wherever the box clips it. Job-3 p164: the oval's whole top cap was erased, the flood
  then walked out through the gap and measured the region box as free space (a flat 206x205
  profile inside a ~460px balloon), and the first English line was drawn across the arc (~34px).

  `typeset.balloon_from_original` supplies the missing bound: the region's enclosed interior
  measured on the ORIGINAL page, where the outline is still there. The profile becomes the MIN
  of the flood and that interior, row by row, so it can only ever remove space the flood claimed
  and never add any — a page where the two agree is byte-identical to before, a profile already
  tighter than the balloon is left alone, and the region handed to the fitter is never enlarged.
  Refused outright (⇒ previous behaviour) when the outline is open, when it is a pocket rather
  than a balloon, or when the clip would destroy the profile; a crop that CUTS the outline first
  grows and retries, which is what lets a small container inside a big balloon be measured.
- **A block no longer shrinks to fit a tapered balloon — it MOVES.** `_placement_top` slides the
  block to the rows whose profile is wide enough, checking each line at the rows it will actually
  occupy (`_line_metrics` reads PIL's real per-line advance from a reference probe instead of
  assuming uniform bands). The centred position is tried first and the search moves only as far
  as it must, so a balloon that already fits its block is placed exactly as before. This is the
  placement half of what v0.27.34 got wrong: that release tightened the FIT and shrank the
  lettering (job-3 p10 25px → 18px), which is why it was withdrawn.
- **A caption box that lies mostly off the page is clamped, and a box with no on-page area is
  never erased** (job-2 p56: `y+h = 1681` on a 1600px page left a black band with no English; the
  erase is irreversible, so text we cannot letter is left alone). Both paths log.
- **A hyphenated word too long for its line is broken at its hyphen** (`MIO-SAN!` → `MIO-` +
  `SAN!`), comic lettering's own convention. Job-3 p85's 70x140 balloon was lettered at 13px
  where the less accurate `MIO!` had filled it at 28px. Tokens without a hyphen are untouched —
  the fitter's "force one word onto a line, never loop" contract still holds — so every other
  wrap stays byte-identical.
- **A region whose centre lands on ink still gets a profile.** The flood used to give up when the
  crop's centre pixel was not light (art inside the balloon, an un-erased remnant), silently
  handing that block the rectangular fit, which has no idea where the outline is. It now seeds
  from the nearest light pixel.

### Added
- **Replay harness** — `app/pipeline/replay.py` (capture, off unless `/tmp/mf_replay` exists)
  plus `scripts/replay_typeset.py` and `scripts/replay_diff.py` in the skill. A re-render
  re-translates, so two renders differ by WORDING as much as by code and the whole-library A/B
  harness can only triage; a bundle records the exact inputs of the typeset stage (the inpainted
  page, the original's grey, every block/region/shape decision) so the same page can be rendered
  through two builds and diffed. The diff reports per block the changed ink, the ink outside the
  balloon the ORIGINAL encloses (via the app's OWN `balloon_from_original`, so instrument and
  renderer cannot disagree), and the glyph height — and reports a block it cannot measure as
  UNMEASURABLE rather than as clean.
- Tests: `tests/unit/test_balloon_from_original.py` (the erase-gap geometry, the open-outline
  refusal, the end-to-end "no ink outside" invariant, placement and profile alignment) and
  `tests/unit/test_off_page_region.py`, `tests/unit/test_long_word_break.py`. **347 pass.**

## [0.27.36] - 2026-09-20

### Added
- **A skipped caption or a block that lettered nothing is never silent again.** Both are
  log-only diagnostics; neither touches what gets drawn.
  - `captions.recover_captions` logs every path it takes: each rejection with its reason
    (`CAPTION skipped (kind): kind=TITLE jp='…' box=[…]`), a cluster that was too long, and
    a page that produced no candidates at all. Before this, a rejection went only to the
    fitlog, which is off unless the container was opted in — so a page could come back
    Japanese with no trace of why. That is exactly what happened on job-3 p109 (its
    `【木工職人 キャロ】` was lettered in one run and skipped in the next).
  - `typeset_page` compares the lettering region before and after each draw and warns when
    nothing landed (`NOT LETTERED: region … unchanged after the draw`), including when the
    region is entirely off-page. This is the "erased with nothing drawn" class (job-2 p56's
    caption box, which sits mostly below the page edge) — until now it left a hole in the
    page with no evidence anywhere.
- **Whole-library A/B harness** for judging a lettering change: `scripts/ab_library.py` in
  the `manga-fill` skill (dev tooling, not shipped in the app). It compares two render
  trees block by block across an entire job and ranks SIZE-DROP / SIZE-GROW / CROSS-UP /
  MISS-NEW per block — ink that landed in the block's own box (polarity-agnostic, so the
  white-on-dark stat cards count), glyph height against the ORIGINAL's glyphs in the same
  balloon, centring and raggedness, and ink outside the balloon. 193 pages / 2624 blocks in
  ~2 minutes. The v0.27.34 regression was shipped off a 20-block sample; this replaces that
  sample with the whole volume.

## [0.27.35] - 2026-09-20

### Reverted
- **The v0.27.34 positional shape check is WITHDRAWN.** It cost up to 28% of the font size on wide
  balloons and did not fix the defect it was written for. Measured on job-3 p10 with the fitter's
  own trace — same page, same blocks, old code vs new code:
  `Then I'll hold back no longer.` **25px → 18px** (wrap 261 → 174),
  `It'll be tough for a while, but…` 25 → 19, `-And so my training began…` 30 → 28, plus three more
  blocks 1px down. On that page the balloon is left mostly empty, which is a worse reader outcome
  than the overrun the clause removed.
  - The clause was geometrically *right* and rhetorically wrong: that 25px line really did cross the
    outline, so containment rejected it and the fitter fell back to a small, sparse layout. The
    trade was the problem, not the measurement.
  - Root cause of the over-correction: containment compares the line — drawn centred on the region
    axis — against the interior's per-row extents, and on an asymmetric balloon (a tail or bulge
    moves the area centroid off the row centres) the binding side is far narrower than the row's
    width. The width test never bound there, so the change was not the "strict refinement" it was
    documented as. Doing this properly needs placement that follows the balloon's shape (per-band
    centring, or a wrap that adapts to the taper), not a stricter width test.
- `app/pipeline/typeset.py` restored to the v0.27.33 fitting logic; the three test files it touched
  restored; `tests/unit/test_typeset_containment_v2734.py` removed. Suite: **335 passed**.
- **Viewer page-jump stays** (unaffected by the revert).
- **Job-3 p164 is unchanged by this revert and still overruns its balloon.** That one is the profile
  problem — the block's own erase box removes the balloon's outline before the interior is measured
  — and no fitting tweak can fix it.

## [0.27.34] - 2026-09-20

### Added
- **Jump to a page in the viewer** — the page number in the Prev / n / Next strip is now an
  editable box: type a number and press Enter (or click away) to go straight to that page.
  Out-of-range numbers clamp to the first/last page, and a junk entry restores the page you
  were on. Arrow keys still page back/forward while the viewer has focus, but no longer page
  the moment you are typing in the box.

### Fixed
- **Lettering can no longer be centred into rows that do not sit under the balloon's axis.**
  The shape test compared each wrapped line's WIDTH with the balloon interior's narrowest row
  in that line's band, but the lettering is drawn centred on the region box's axis —
  `_region_avail` re-centres the box on the interior's area centroid, and on a tapered or
  asymmetric interior a row can be narrow yet offset from that axis. A line that was narrow
  enough for its rows could therefore be drawn across the outline. Measured on job-3 p110
  (where it changed the fit) with the fix's own unit tests proving the clause rejects a layout
  the width test accepts, and left unchanged every shape whose lettering was already correct.
  - The check is CONTAINMENT against the interior's raw per-row extents, i.e. the outline
    itself, with only the halo's thickness as allowance. That keeps it consistent with the
    existing width tolerance: `_lines_fit_shape(…, rows=None)` still reproduces the previous
    behaviour exactly, so nothing else in the pipeline moves.
  - Cost, measured over the 20 blocks of job-3 pages 110/164 that a re-render re-fit:
    **7 blocks 1–3px smaller, 13 unchanged, none larger** (e.g. 20→18, 13→11, 11→9, 19→18).
  - **This does NOT fix job-3 p164**, whose lettering still crosses the balloon's top arc.
    Measurement on that page (arc at x371/x520 on the rows where the first line's ink spans
    x337–x522) shows the profile handed to the fitter is itself wrong — see the next release
    note / KNOWN ISSUES. Chasing that is a separate, larger change.

## [0.27.33] - 2026-09-20

### Added
- **In-world captions on pages with no dialogue are now read and translated** (Settings →
  *In-world captions on cover/credit pages*, on by default). A page whose text is entirely
  horizontal — a cover, a credits or colophon page, or a splash page carrying a 【…】 name-tag — is
  left in Japanese by the horizontal-text gate, and that gate is deliberate: those pages were
  damaged when they ran through erase + lettering (v0.16.1/v0.16.2: logos erased and never
  re-lettered, credits overlapped, the model hallucinating titles). But the gate also dropped
  IN-WORLD captions that a commercial release does letter — job-3 p159's 【給仕 キーマ】 is
  "[SERVER: KEEMA]" in the Ichigo edition while we produced nothing at all. With this setting on,
  the configured model (the same endpoint as translation — nothing new to configure) **reads the
  crop** and translates only in-world captions and name/role tags.
  - **Credits, colophon, publisher and title text are still left exactly as they were**, enforced
    twice: a wording filter (原作 / 発行 / 株式会社 / レーベル / © / ● …) and the model's own
    TITLE / CREDITS verdict. Both were exercised on the real pages (p192's colophon, p3's cover
    credits) and neither was touched.
  - **A garbled reading can never become a translation.** manga-ocr is the component that fails on
    this stylised text (p159 came back as *three* boxes, one of them a 27-character hallucination),
    so a reading is only accepted when it physically fits the box — `capacity()` computed from the
    measured glyph height and width, since the Japanese path carries no OCR confidence to threshold.
  - **The caption's own ink band is what gets erased and lettered**, not the padded cluster, so
    decorative rules beside a caption survive (p159 has a diamond-pattern rule directly under it).
  - Costs one vision call per candidate caption, only on those pages. A provider error, an
    unreadable crop or an implausible reading all leave the page untouched.
- Instrumentation: `CAPTION` records in the fit log for **applied and rejected** captions (with the
  reason), a `captions=` counter in the FITLOG page summary, and `caption_vlm=<bool>(raw)` on the job
  start line.

### Verified
- **335 unit tests pass** in the container (317 existing + 18 new in `tests/unit/test_captions.py`,
  which are mostly about what the feature must REFUSE to touch).
- Real pages, real OCR blocks, live model: p159 (3 junk boxes → 1) lettered **`[SERVER: KEEMA]`**;
  p109 (`職人キャ` + 40 chars of junk) lettered **`[WOODWORKER: CARO]`**; p192's colophon and p3's
  cover credits both **rejected** (series title → TITLE, ®/© line → CREDITS) and left untouched.
- End-to-end job on the isolated instance: 4 pages — p159 lettered and translated, the credits and
  colophon pages 0 translated, the dialogue page unaffected (14/14).

## [0.27.32] - 2026-09-20

### Fixed
- **Lettering was centred on the balloon's bounding box rather than on the balloon.**
  Follow-up to v0.27.31 from the user's own read of the re-rendered page 7: the
  "This time he collapsed just from lightly running around the garden..." block still
  looked left-aligned against its bubble. Measured on the untouched original scan: the
  balloon interior's **bbox centred on x=451**, while **every row of the balloon
  centred on x=468-470** — so bbox-centred lettering sat ~18px left of the balloon. The
  commercial reference centres that block at **x=469.5**. The interior is asymmetric
  (it reaches x=338 at some rows while its rows centre at ~469), which drags the bbox.
  `_region_avail` now returns a box of the interior's own size **re-centred on the
  interior's width-weighted centroid**, and the per-row lettering space is the
  interior's **own extent** in that row instead of a run measured from the bbox centre.
  Verified on the real page: that block moves 452 -> **468** (within 1.5px of the
  reference) while the page's other seven balloons move only 0-4px, i.e. only the
  genuinely off-centre blocks move. Applies to **ja, ko and zh** — all three language
  paths mark their blocks `shaped` and go through this one code path.
- **The outline tolerance is now 1.10, chosen by measurement.** A 20% tolerance let the
  lettering escape a spiky balloon: with container fonts, 1.10 -> 0px of ink outside the
  outline, 1.15 -> 122px outside, 1.20 -> 285px. 1.10 is the largest value that keeps
  the ink inside on both a spiky balloon and an oval, and still lifts an oval from
  18px/10 lines to 19px/10 lines.

### Added
- Fit logging: `anchor_dx` alongside `anchor_dy` (how far the box moved off the
  interior's bbox on each axis), so an off-centre placement is visible in the dump
  rather than only to the eye.

## [0.27.31] - 2026-09-20

### Fixed
- **Lettering is now as large as the balloon actually allows, and centred on the
  balloon rather than on its bounding box.** Measured against the commercial English
  release of the same book (Ichigo, *Tsukimichi v11*), job-3 page 7 lettered a 226x274
  balloon at **12px** where the reference uses **18px**, and across the page's 14
  balloons our ink inside the balloon was **0.057 vs 0.074 (0.77x)** — the "still lots
  of white space" complaint.
  Two causes, both fixed:
  - **Size.** `_wrap` wrapped to the balloon's WIDEST row (`avail.max()`) while
    `_lines_fit_shape` then required every line to fit the NARROWEST row of its own
    band. On a tapered oval the two rules fight, so every size above a small one was
    rejected — the fitter was taking the largest size that passed, and nothing larger
    could pass. Now the wrap width is chosen from the shape as well (`WRAP_FRACS`), and
    the per-band check carries a tolerance (`SHAPE_TOL = 1.2`) because trade lettering
    sits close to the outline (the commercial `manga2eng` renderer uses the same 1.2).
    On a modelled balloon of the real geometry: **14px -> 16px, ink fill 8.9% -> 12.0%**,
    with **0.00%** of the ink outside the outline.
  - **Placement.** The block was centred on its bounding box, which a tail or a
    squashed outline pulls off the visual centre — measured on page 7, our ink sat
    24px lower than the reference in the same balloon. The block is now centred on the
    balloon interior's own vertical centroid (the row-centroid of its width profile),
    so the slack is split either side instead of pooling at one end.
  - `_lines_fit_shape` clips its band rows to the profile's length: `avail` has one
    entry per interior row while the fitter is handed `region_h` from the region box,
    and on a looser box the mismatch indexed past the end, making every candidate look
    like a failure.
  - The size rule is now **size-first** inside the guaranteed band: it takes the
    largest size that fits (at most one step below `largest_fitting`) instead of the
    highest-fill one, which preferred a tall stack of short lines (measured: 15px over
    10 lines where 16px over 8 fitted — the "word list" look).

### Added
- Fit logging now records HOW a fit was reached: `wrap` (the winning wrap width),
  `frac` (as a fraction of the balloon's widest row), `shape_tol` and `anchor_dy` (how
  far the ink was moved off the bounding-box centre), plus per-page `narrow_wrap=`,
  `anchored=` and `anchor_shift=` counters in the `FITLOG` summary line. Without these
  a dump cannot tell a block lettered to the widest row from one lettered to a narrow
  column, which is what made this defect take so long to see.
- Tests: `tests/unit/test_typeset_fill_v2731.py` pins the observable behaviour — the
  wrap-width choice must grow the lettering, ink must stay inside the outline, the
  anchor must follow the balloon's centroid rather than its bbox, and the band clamp
  must not reject candidates.

## [0.27.30] - 2026-09-19

### Changed
- **Logging: a handled recovery is no longer counted as a defect.** `degenerate=N` used
  to conflate "thin region, unhandled" (a real defect) with "thin region, recovered"
  (fine), so the Logs tab could not tell the two apart. They are now separate
  (`degenerate=` vs `recovered=`), and every recovery also emits its own
  `FITRECOVER page=... old=... new=...` line so a correction is visible to an operator
  rather than buried in a counter.

## [0.27.29] - 2026-09-19

### Fixed
- **Class C: a region far too small for its text is now recovered from the erase
  footprint.** Large hand-lettered (brush) lines get detected — and region-resolved — as
  thin strips, so the English was lettered at ~8px over a large blank area. Measured on
  job-3 page 163: box `[702,457,197,39]` for まったく上がらす！, region squeezed to
  `197x8`. The source text is erased correctly, so the honest region is "at least what
  we erased": `_recover_thin_region` unions the block's own erase rects and uses that
  when it is bigger than the region and still sane — never page-sized, never beyond 8x
  the region area or 6% of the page, and never such that another block's box would be
  swallowed. The recovery is logged as `degenerate/recovered_thin_region` with the old
  and new boxes, so the count is auditable rather than something you have to notice.
- `_recover_thin_region` is wrapped at its call site: instrumentation-adjacent failure
  must never break a render.

### Added
- `tests/unit/test_class_c_region.py` — 7 tests: the real p163 case, no-op on a sane
  region, short-text labels left alone, page-sized footprint rejected, area cap, refusal
  to swallow a neighbour, and never-raises.

## [0.27.28] - 2026-09-19

### Added
- **Logging: class-C detector, erase footprints, and a build stamp.** Three additions
  so the remaining defects are measurable rather than noticed:
  - `degenerate` records — a lettering region far too small for its text. This is the
    newly identified **class C** defect: a large hand-lettered line detected as a thin
    sliver (measured: **197x8** for brush text ~70px tall), the Japanese erased, and the
    English lettered at the 8px fallback inside the sliver — leaving a blank area where
    big text used to be. Page 163 of job 3 is a live example.
  - `erase` records — the erased pixel area per page. Blank-area defects are only
    diagnosable with the erase and the lettering sizes side by side.
  - a `meta` record naming the exact version that produced a dump. A dump without it is
    untraceable, which is how a container was found running a **mix** of file versions.
  - the per-page `FITLOG` line now carries `unmeasurable=` and `degenerate=`.

### Fixed
- **A stale fit trace could be reported against the wrong block.** When the fitter bailed
  out early (a degenerate region) the previous block's `largest_fitting` was logged
  against this one, producing phantom audit failures. The trace is now invalidated at
  entry, tagged with the text it belongs to, and carries `trace_ok` so the audit counts
  such blocks as *unmeasurable* instead of inventing a gap.

## [0.27.27] - 2026-09-19

### Fixed
- **"Big balloon, tiny text": the fitter's own rule was throwing the size away.**
  `typeset._fit_common` had a HARD exclusion for the stacked "word list" look
  (3+ lines, nearly all single words). Bigger letters wrap to *fewer* words per line,
  so that exclusion deleted precisely the largest candidates — and the fill
  maximisation then ran only over the small ones. Measured on a 12-page sample
  (176 blocks): **20% of blocks under-sized, mean +31%, worst +100%** — "That makes
  three." lettered at **9px where 18px fitted**, and a 148x234 balloon holding 10px
  text. On job-3 page 8 the smallest block was 10px and 2 blocks were under-sized.
  The objective is now a single ordered rule: only sizes that FIT are candidates; the
  chosen size may be at most **one step** below the largest that fits; inside that band
  score by covered area, then fewest one-word lines, then size. Avoiding a stacked look
  is worth at most one size step, not a third of the font. Page 8 measures
  `size_min 10 -> 14`, `under_sized 2 -> 0`, with no outline crossings introduced.

### Added
- **Fit instrumentation (`app/pipeline/fitlog.py`), opt-in per container** via
  `docker exec <container> touch /tmp/mf_typeset_debug`. Records per block the region
  resolution branch, the shape profile, **every font size considered with its reject
  reason**, and the chosen size vs the largest that fitted; plus a one-line per-page
  summary to the app log (`FITLOG page=... blocks=... under_sized=... branches={...}`)
  so the behaviour is visible in the Logs tab. Off by default (one `os.path.exists`
  per page) and exception-proof: instrumentation must never break a render.
- **Whole-job audit harness** (`mfqa/audit_fit.py`): ranks every block whose size fell
  below the geometry by severity and exits non-zero on failure, so a release can be
  gated on it. This is the objective check that replaces eyeballing pages — a 193-page
  visual sweep missed the page-8 class entirely.

## [0.27.26] - 2026-09-19

### Fixed
- **A *stacked* drawn sound effect was still lettered at the page-width cap — erased
  art came back as one tiny word.** v0.27.6 routes a drawn SFX through its own tight
  box so the font comes from the footprint instead of the page (`width/32` = 21px on a
  690px webtoon), but `render._is_drawn_sfx` only accepted blocks the classifier
  labelled `horizontal`. A stacked SFX — three big glyphs running down a page cut — is
  labelled `vertical`, so it fell through to `_caption_region`, where the **page cap
  still applied**: job-2 page 40's `더다다` (476x792) lettered at **21px** while the
  identical construct on page 39 (`튼다다`, 502x735, labelled `horizontal`) lettered at
  **109px**. A box that is wide enough to hold horizontal English (`w >= 0.55h`) and
  large (`>= 60000px²`) now takes the drawn-SFX path too: page 40 measures 21px -> 61px,
  centred inside the erased footprint and clear of the panel edges. Tall-narrow columns
  (job-1 page 5's 72x461 tategaki caption) and label-sized boxes keep the caption
  treatment — English cannot fill a vertical column. The predicate was dry-run over
  every stored ko/zh block of jobs 1-3 and flips **exactly this one block**, so no
  other page re-letters.

## [0.27.25] - 2026-09-19

### Fixed
- **Lettering was sized to the detector's bubble box instead of the balloon — the
  English came out small with the balloon's lower half empty.** On job-3 page 12 the
  detector named a 204x311 box for a balloon that is actually **307x413** — roughly
  half the area — and the Japanese path lettered into that box and nothing else:
  the font was capped by the smaller rectangle, and centring on it put the text high
  in the balloon with a large empty gap below. The ko/zh path never had this problem
  because it floods the white interior to find the real balloon; the ja path now does
  the same. It floods the interior (`find_container`), and **prefers it when it
  plausibly belongs to this text column** (`render._plausible_balloon`: the flood
  must contain the column, not be page-sized, and not be wildly larger than the
  detector's box — a flood escapes any balloon whose outline has a gap). Measured on
  page 12: the account balloon went 204x311 -> 307x413 and the font grew accordingly,
  and two balloons that had rendered **empty** now carry their text.
- **Regression guard for the same page: lettering must not cross the outline.** With
  the region now the real (larger) balloon, the v0.27.15 rectangle "safety net" in
  `typeset._draw_box` began winning over the balloon-shape fit and pushed text across
  the curved black outline. That net exists for a different failure — a *fragmented*
  `avail` profile (art/lettering inside the balloon breaks the light region into
  slivers) — so it now only applies when the profile really is fragmented
  (`avail.max() < 0.6 * rect_w`). A clean profile keeps the outline-aware fit, which
  fills the balloon without spilling.

## [0.27.24] - 2026-09-18

### Added
- **Audit trail — every user and system action is now logged.** Destructive actions
  left no trace at all before this: "Clear all jobs" deleted every job and said
  nothing in the log. New `app/services/audit.py` records each action to **two
  sinks** — the app log file (which `/api/logs` tails, so it shows up in the Logs
  tab) and a durable `audit_log` table — and distinguishes the **actor**
  (`user` = an API call, `system` = the engine acting on its own). An HTTP
  middleware records *every* POST/PUT/PATCH/DELETE with its status and duration, so
  nothing is missed even on an endpoint nobody instrumented; the important actions
  also emit a richer semantic entry (Clear all reports how many jobs and that it is
  irreversible; delete / resume / pause / stop name the job; a re-render reports the
  page count; the engine logs `job.finished` with page totals and failures).
  A failing audit write can never break the action it describes. New `GET
  /api/audit?limit=` returns the durable rows newest-first. GETs are deliberately
  not logged — the dashboard polls `/api/jobs` every few seconds and those would
  bury the real actions.

### Changed
- **Upload page: the file picker is now a proper dropzone.** The bare
  `<input type="file">` rendered the browser's LIGHT default control ("Choose
  Files" / "No file chosen") sitting inside the dark form — the same unthemed-control
  bug as the old viewer button. The Files field is now a themed dashed area that
  states it accepts drops ("Drag & drop your pages here — or click to browse —
  .jpg .png .webp, or a .cbz / .zip"), highlights in the accent colour while a drag
  is over it, lists the selected filenames (first 6 + "+N more"), and is keyboard
  reachable (`:focus-within`). The real input sits transparently on top of the area,
  so a click anywhere opens the picker and `required` field validation still has a
  focusable control. Dropped files are filtered to the accepted extensions, with
  unsupported ones reported rather than silently ignored. The `input[type=file]`
  selector was removed from the shared field rule so the native control can't
  reappear.
- `pyproject.toml` version was synced (it had drifted, still reading 0.27.21).

## [0.27.23] - 2026-09-18

### Fixed
- **Tall balloons with art inside no longer leave the English top-aligned.** A balloon
  with a chibi / face / hand drawn in its lower half splits the white flood-fill, so
  neither the RT-DETR detector nor `find_container` recovers its interior — and the
  block fell back to lettering its tight text box, which sits at the TOP of the tall
  balloon, leaving the English hugging the top edge with a big empty gap below (job-3
  p15's "THAT'S RIGHT!..." balloon). `bubble.find_balloon_gap_tolerant` now recovers
  the balloon's full vertical extent by scanning the text column and bridging over
  interior art blobs (a chibi's strokes have white between them; the balloon outline
  is where the white stops for good), so the lettering is centred in the whole balloon.
  It only fires as a last resort — when the detector finds no bubble AND
  `find_container` fails — and it rejects a span that runs to the page edge, one that
  reaches down into the interior art (the bottom is clamped to the last mostly-white
  row, so the English never lands on the chibi), and one far taller than the text
  column (a chain of balloons / the page background, not one balloon). Measured over
  67 vertical blocks on job-3 pages 11-15: it fires exactly once, on the tall
  interior-art balloon on p15; the smaller boxes near the chibi and the working
  stacked-balloon pages (p12) are untouched. 3 new unit tests; 265 pass.

## [0.27.22] - 2026-09-18

### Fixed
- **Two stacked speech balloons no longer print their English through each other.**
  The detector returns two overlapping balloon boxes for balloons that touch (or one
  figure-eight), `find_parent_bubble` maps each dialogue block to its own balloon, and
  the typesetter centred each into the whole of its balloon — so where the bboxes
  overlapped, the two texts interleaved (job-3 p45 "ATTEMPT" through "IN MIND-", p72
  "PRACTICAL" through "ADVERTISE", p12 "AT BOTH" through "INCHES").
  `_partition_shared_bubble` now reconciles a vertically-overlapping balloon pair by
  splitting the shared band at its midpoint (the upper region stops there, the lower
  starts there), so each block is centred into its own half. It only fires on the
  genuine stacked case — the regions sharing a vertical band of ≥40% of the smaller
  one — so balloons that merely nestle (a few px of rounded corner) are untouched.
  Measured over the 13 worst pages with the real detector + fitter: drawn-English
  overlap **45,738px² → 0**, no page made worse, no block lettered larger.
- **A large vertical brush title is no longer erased and replaced with a mistranslation.**
  `_drop_titles` only caught horizontal titles, so a big vertical brush title (job-3
  p187's 月夜に提灯, 379×437) was read as vertical dialogue, OCR'd WRONG (提灯 → 提出,
  "lantern" → "submitted"), erased and lettered as "SUBMITTED ON A MOONLIT NIGHT". A
  real vertical dialogue column packs many glyphs into its area; a brush title has a
  few large strokes. Measured over every job-3 vertical block: the title is the ONLY
  one ≥100,000px² AND under 0.8 glyphs per 10k px² (0.3; the next-lowest real dialogue
  is 1.08). Large + glyph-sparse vertical blocks are now left as art.

## [0.27.21] - 2026-09-18

### Fixed
- **Lettering a long line no longer stalls the render.** `typeset._wrap`'s
  minimum-raggedness DP measured every candidate line by BUILDING AND MEASURING the
  joined string — ~1ms each, O(n²) of them, re-run for every font size `_fit_common`
  tries. A 47-word caption cost **31s per fit** (job-3 page 5 spent 103s of its render
  in two such fits, which is why a dense page took minutes). Line widths now come from
  each word's own extent, the inter-word space and a per-word-boundary kerning delta,
  each measured ONCE: **page 5's fits 102.9s → 2.8s (37x)**.
  Verified **byte-identical wrapping** over 4,425 (text, width, size) cases drawn from
  all three jobs — 0 differences — so no existing page's lettering changes; this is a
  pure speedup. The per-boundary kerning term is what makes it exact: summing word
  widths alone shifted ~1.6% of wraps by a pixel-level DP tie.

## [0.27.20] - 2026-09-18

### Fixed
- **A widened caption strip is no longer lettered over the artwork or over the
  neighbouring column's English.** Free-floating vertical text with no speech box
  (`_caption_region`) was widened into a BLIND rectangle: on job-3 page 19 a 112px
  column's strip grew 153px to the RIGHT — straight over the next column's text — and
  its 1.5x height expansion lifted the lettering out of the panel, so two English
  letterings were printed through each other across the panel rule. The strip is now
  grown from the source box into free space only, and **symmetric** along both axes so
  the English stays where the source text was (the old strip was anchored at the
  column's left edge and centred 1.5x-tall). It stops at ink — a 3px run or 6% dark
  across the band catches glyph strokes, artwork and 1-2px panel rules while light
  screentone stays usable lettering space — and at every other block's source box or
  already-claimed region on the page. The same code path serves the ko/zh caption
  fallback.
  Measured on job 3 page 19 with the real detector and the real fitter: **drawn-English
  overlap 10,220px² → 0**, regions covering artwork without erasing it 7 → 5, no block
  lettered larger, 3 blocks lettered smaller (they now fit the space that exists).
- A stored block box that runs off its page (job-3 page 40 carries `x+w=797` on a 690px
  page, recorded by an older render) no longer raises `IndexError` out of the strip
  maths — the box is clamped to the image.

## [0.27.19] - 2026-09-17

### Fixed
- The re-render button's busy state now LOOKS inert: while it reads "Queued…" (disabled
  for minutes on a slow page) it takes explicit muted colours, not just a 0.6 opacity —
  the opacity route is easy to lose against the accent styling.

## [0.27.18] - 2026-09-17

### Fixed
- **The viewer's "Re-render page" button no longer looks pasted on.** It was a bare
  `<button>` outside the styled `.viewer-nav`, so it fell back to the browser's LIGHT
  default: white block, black text, square corners, on a dark bar. Every control in the
  viewer bar now shares one base style (`background/border/radius/padding/hover/
  focus-visible/disabled`), the re-render button is a themed accent-tinted ghost that
  fills on hover and shows a busy look while it reads "Queued…", and the close ✕ is a
  38px icon button tinted on hover.
- Prev / n / Next are now one segmented control, the position is tabular-nums so it
  stops twitching between pages, and the title truncates with an ellipsis instead of
  pushing the controls around.
- The viewer bar is opaque (with a drop shadow); it used to show the dashboard header
  through the translucent overlay behind the buttons.

### Changed
- **Phones (<=760px):** the viewer bar wraps (title on its own row, controls below) and
  the Original / Translated panes stack vertically, so a page is readable instead of
  being squeezed into half the screen.

## [0.27.17] - 2026-09-17

### Fixed
- **The cost tally counts "today" in the local timezone, not the container's clock.**
  The window boundaries defaulted to UTC because the app had no timezone configured
  (the container runs with no `TZ`), so a Sydney user's "Day" total could be a day out.
  `app.config.settings.timezone` now defaults to `Australia/Sydney` (override with
  `MANGA_FILL_TIMEZONE`, or `TZ`), and `resolve_tz` falls back through explicit query
  param -> app timezone -> `TZ` -> the system zone -> UTC.

## [0.27.16] - 2026-09-17

### Fixed
- **Downloads now include pages re-rendered after the first download.** The archive
  (`data/jobs/<id>/translated.<cbz|zip>`) was built once and reused forever
  (`if not os.path.exists(arc)`), so every page re-rendered later — a pipeline fix
  applied with Re-render, a resumed page — never reached the file the user received:
  the download kept serving the ORIGINAL render. The archive is now a cache keyed on a
  manifest of the output pages (`translated.<ext>.manifest.json`, name + mtime_ns +
  size), rebuilt whenever a page changes, is added or is deleted, written to a temp
  file and moved into place so a concurrent download never sees a partial zip, and
  served with `Cache-Control: no-store` so the browser cannot hand back the old one.
- **`GET /api/models` no longer returns the API key in full.** The raw key was in the
  JSON, the DOM of the Settings tab, and every screenshot of it. It is now masked
  (`sk-abc…6789`) with a separate `api_key_set` flag; the editor shows an empty field
  with "leave blank to keep the stored key", and a blank or masked value submitted back
  keeps the stored key instead of overwriting it with the mask.

### Added
- **Cost tally with a Day / Week / Month / Year selector (defaults to Day)** on the
  Jobs tab: the total spend for the selected CALENDAR window in the app's timezone
  (today, this week from Monday, this month, this year), plus jobs / pages / tokens for
  the window and the all-time total next to it. New endpoint `GET /api/stats/cost`
  (`?period=day|week|month|year&tz=Australia/Sydney`). A job's spend is attributed to
  the window containing `finished_at`, else `updated_at`, else `created_at`. When the
  model's rates are 0 the UI says so instead of implying the work was free.

## [0.27.15] - 2026-09-17

### Fixed
- **Lettering now sizes itself to the box it is given, for all three languages.** The
  fitter used to take "the largest size that fits *and* carries at most one one-word
  line", so whenever a tidy wrapping existed anywhere below the cap it took that and
  left the balloon mostly empty — audited on job 1 (Chinese manhua, real blocks): an
  `Ah` in a 133x138 bubble lettered at 36px (13% of the box), `I'm sorry, big sister-`
  in a 269x475 balloon at 25px (9%), a vertical `Great fortune` in a 196x313 box at
  14px (3%), `I won't go back with you!` in a 244x403 balloon at 27px (17%). Every
  fitting size is now scored by the area its wrapped block covers and the largest fill
  wins, excluding only the "word list" look (three or more lines, nearly every one a
  single word — `I'm / sorry, / big / sister-`); one-word lines inside a 1-2 line block
  are ordinary comic lettering and stay. Same texts now: `Great fortune` 19px -> 35px
  (one line -> two, filling the tall box), `I've finally received my first mission in
  life.` 27px -> 35px, a boundary-less caption strip 17px -> 22px.
- **The balloon-outline fit can no longer letter SMALLER than the balloon's inscribed
  rectangle.** `_region_avail` flood-fills the balloon interior, and lettering or art
  inside the balloon splits the light region into slivers, so `avail` reads 0 on most
  rows and every size above the sliver is rejected. A 15%-inset inscribed-rectangle
  fit is now a floor for that path, so this change can only ever grow lettering.
- **The erasing inset came in from 15% to 10%** of the region's smaller side, so
  lettering uses the space it was fitted to.
- **The boundary-band OCR pass no longer overrides a better native read.** Page 29 of
  job 2 (Korean webtoon) reads `발단은` (221,1365,250,110) and `5년전` (218,1473,252,116)
  at conf 1.000 in the page pass; the lookahead band — the page's last 500 rows joined
  to the next page's first 500, rescaled — read the same pixels as the single junk glyph
  `긍` at conf 0.952. Every page box starting inside the band was dropped in favour of
  the band's reading, so that junk block became the page's only text: the pipeline erased
  one line's box, translated `긍` as "Mm." and lettered it over the Korean that survived.
  The page's own native read is now authoritative for text that sits inside the page;
  `_band_handover` still lets a band box that CROSSES the page cut supersede the page's
  partial view of that boundary bubble, and still drops band duplicates of page boxes.

## [0.27.14] - 2026-09-17

### Fixed
- **Erasing a block no longer repaints the artwork behind it.** The erase mask was each
  block's full rectangle, so a block sitting on art came back as a flat LaMa wash: job-2
  page 38's drawn `튼다다` (502x735, over a fire-lit panel) was a grey block, page 84's
  `조~으~옹..` flattened a blue sky, and page 56's narration panel + drawn `치이즈` were
  merged into one 464x656 box that erased the art behind both. Measured on those pages the
  ink is only 9-19% of the box, so the mask is now built from the strokes
  (`inpaint.stroke_boxes`): a per-tile median background, a high-contrast ink mask, small
  enclosed holes filled (a drawn glyph's face reads as background otherwise and survives as
  a ghost), then cell runs with a skirt. Verified by A/B-ing the real LaMa on the GPU
  worker: the fire-lit building, the sky and the shop interior all survive where the box
  mask flattened them. The mask falls back to the plain rectangle whenever the ink fills
  most of the box, so the worst case is the previous behaviour.
- **A drawn sound effect can no longer merge into the narration above it.** Lines of one
  text block share a line height, so `_stack_adjacent` now refuses a merge when the two
  boxes' heights differ by more than 3x. On job-2 page 56 the three-line narration
  (60-70px lines) had swallowed the 384x457 drawn `치이즈` below it: the erase box covered
  both, the translation landed between them, and the narration panel came back EMPTY with
  the English drawn over the erased art. The panel is now lettered inside itself.
- **A drawn-picture mis-read is left as art.** A block whose box is ≥25000px² and whose
  reading is a single glyph, with no enclosing balloon, is a partial read of a drawing
  (`치이즈` came back as `철`, confidence 0.98, in a 384x457 box). Erasing it destroyed the
  art and there was no real translation to letter, so it is left alone.
- **Already-English signage is left alone.** A Korean/Chinese sign carrying English type
  reads back as a Latin-dominant string and was translated as dialogue: job-2 page 46's
  shop front was washed out under "DPENON.ETER 40TH WEEK GRAND OPEN 4/25-12/25 EYET".
  Blocks with no enclosing balloon whose letters are ≥60% ASCII are now skipped, so the
  sign and its artwork stay intact.
- **One balloon holds ONE string in Japanese too.** `_merge_blocks_per_bubble` ran only for
  Korean/Chinese, so a starburst balloon holding two blocks was lettered twice, on top of
  itself — job-3 page 145 carried "HII!" straight through "A TANNED BEARDED OLD MAN WITH A
  WHITE CLOTH...". Merging now covers every language, so the balloon goes to the translator
  as one unit and is lettered once.

### Tests
- 21 new unit tests: `test_erase_and_guards.py` (stroke masks, hole filling, the merge
  height guard, the signage and stub-art guards) and `test_render_erase_wiring.py`, which
  drives `render_translated_page` with the models stubbed out — the wiring test exists
  because a `Image has no attribute 'ndim'` typo in the erase path passed every
  pure-function test and only surfaced when a real page was rendered in the browser.

## [0.27.13] - 2026-09-16

### Fixed
- **The detector no longer treats halftone screentone as free-floating text.** RT-DETR-v2
  fires repeatedly over a screentone patch, and manga-ocr then reads a plausible phrase
  out of the dots, so the lettering was drawn onto clean artwork — job-4 page 118 carried
  five overlapping copies of one line, and pages 144/43/152/47 were speckled with the same
  phantom family (`そういえば`/`そういうこと`/`それでも`). A narrow-and-tall `text_free`
  region must now clear a real detector score (width ≤ 48px, height ≥ 80px, score < 0.44
  → dropped); every other region keeps the deliberately low 0.2 threshold that exists so
  tiny single-character SFX (`ほえ`, `はっ`, `コヒュ` at 0.3-0.45) survive.
  Measured over nine such pages at a 0.05 threshold: every phantom detection is
  `text_free`, 13-48px wide, 81-560px tall, score 0.20-0.43, while genuine free text on
  the same pages reaches 0.94 and only dips below 0.44 when it is compact or wide (a
  34x130 chart caption at 0.451, a 51x154 drawn SFX at 0.253, 51px-wide mutter columns at
  0.231). The 48px ceiling splits the measured width gap (phantoms ≤48px, narrowest real
  free text 51px). 67 of 68 phantom detections are filtered, and the four real-looking
  blocks the rule also drops were each checked at 5x zoom — all four are screentone.
- Implemented in the GPU worker (`gpu-worker/models.py`, worker 0.3.2) and mirrored in
  the app's own detector so a CPU-only install behaves identically. `/detect-ocr` now
  reports `dropped_halftone`, so the filter is visible in the response instead of
  silently losing regions.

### Changed
- `detect_containers` carries the detector scores through (previously discarded) — that
  is what makes a score-aware floor possible at all.

### Notes
- **Needs a GPU-worker rebuild to take effect in production** (`docker compose build &&
  docker compose up -d` on the worker host — see the worker's SETUP_GUIDE). Until then
  deployments keep worker 0.3.1, where the v0.27.12 duplicate collapse already limits
  this defect to one lettering per region.
- Residual risk (none seen in testing): a phantom strip WIDER than 48px or SHORTER than
  80px with a weak score still letters — the window has to stop somewhere, and it stops
  where the measurements separate halftone from the narrowest genuine free text (51px, a
  drawn バキニ SFX). Catching that tail needs a model-side change, not a threshold.

### Verified
- 204 unit tests pass (6 new: the measured phantom boxes are filtered, the measured real
  free text survives, the boundary is exclusive at the floor, a missing score never
  filters, `bubble`/`text_bubble` are never touched by this rule, and `detect_containers`
  counts what it drops).
- The modified worker was run **on CPU** (no GPU needed, nothing in prod touched) against
  the nine pages that produced the phantoms: `dropped_halftone` fires on each (0 on a
  clean control page, 3-65 on the affected ones) and every phantom-family reading
  disappears except page 113's 51px leaf patch noted above.

## [0.27.12] - 2026-09-16

### Fixed
- **The same line could be lettered several times over one spot — onto artwork that
  holds no text at all.** Manga-ocr's box detector fires repeatedly over halftone
  screentone and returns 2-6 overlapping boxes that all read the same phrase (job-4
  page 118: six `そういえば`/`そういうことで` boxes across 80x240px of pure dots — at 5x
  zoom there are no characters there at all). Each copy was translated and typeset, so
  the page carried five overlapping "SPEAKING OF WHICH," drawn over a clean panel.
  `_collapse_duplicate_blocks` now clusters boxes that overlap by more than 25% and
  read as the same utterance (punctuation-insensitive: `そういえば、` and
  `そういえば．．．` are one line), keeps only the most complete reading, drops a reading
  that is merely a truncated version of a fuller one, and — when 3+ boxes in a cluster
  read *identically*, which never happens for genuine text — discards the whole run
  including any short block overlapping its footprint.

### Notes
- Deliberately **no pixel-texture test** for "is this really text?": a "no glyph
  strokes ⇒ halftone" filter was built and measured against the real blocks and would
  have dropped 443 job-4 blocks — including correctly-lettered SFX (`啪`→SMACK,
  `啊`→Ah, `主人`→Master.) — because a real thin-stroke glyph and a halftone dot are
  not separable that way. A phantom that is detected exactly **once** is therefore
  left alone: it is indistinguishable from real text without a detector-side signal.
  Residual measured on job 4: single detections of this family still letter (page 144
  has several across its panels). Stopping those needs a confidence/size floor in the
  GPU worker's `/detect-ocr`, tracked as a follow-up.

### Verified
- 198 unit tests pass (12 new, using the real failing boxes: the page-118 run, the
  page-113 cluster, the page-60 truncated duplicate, and the cases that must be
  untouched — the same line in two balloons, two `ふる` SFX boxes clipping a corner,
  distinct adjacent columns, a full line next to a phantom run).
- Dry run over every stored block of jobs 2/3/4: job 2 collapses 0, job 3 collapses 0,
  job 4 collapses 30 — all of them the screentone phantom family, none a reading longer
  than a short phrase.
- Re-rendered the affected job-4 pages and compared ORIGINAL | BEFORE | AFTER crops.

## [0.27.11] - 2026-09-16

### Fixed
- **A single stylized glyph could be misread as a short word and lettered as one.**
  Reviewing the v0.27.10 recoveries on the real pages caught job-4 page 88: the
  balloon holds a brush-drawn 響 (a name) and manga-ocr returned `それぞれ、` — the
  balloon was lettered "EACH OF YOU," instead. `_ink_matches_length` checks the OCR
  result against the ink the balloon actually holds: a line of characters needs far
  more ink than one glyph, so when the interior holds less than ~5% ink per character
  (measured: one glyph = 1.4-10.5% of the inset; the misread's four kana would need
  ~20%) at most two characters are accepted. Page 88's balloon is left as-is again
  while the reported 真 (page 7) still recovers. Erring high only leaves a balloon
  Japanese; erring low letters a wrong word into it.

### Verified
- 186 unit tests pass (3 new: punctuation-free glyph counting, the page-88 misread vs
  a real single glyph, and the recovery rejecting the misread while keeping 真).
- Re-scanned the affected pages: page 88 recovers 0 balloons (misread rejected), page 7
  still recovers 真.

## [0.27.10] - 2026-09-16

### Fixed
- **The v0.27.9 missed-bubble recovery was too eager — it could letter a balloon
  twice and could OCR artwork.** A scan over job 4's 193 pages flagged 19 candidate
  balloons; reviewing every one on the real pages showed two failure modes:
  - **Duplicate lettering.** A detection box can cover only PART of a balloon (page
    92: the `bubble` box was the top line of a 3-line caption, so the caption block's
    centre fell below it). The old "is any block's centre inside the bubble?" test then
    reported "no text here" and the balloon was lettered a second time — English over
    English. `_bubble_covered` now decides by **overlap** (a block ≥25% inside the
    bubble, or ≥0.15 IoU), which catches the partial-box case.
  - **Artwork misread as dialogue.** manga-ocr hallucinates plausible Japanese from
    line-work and screentone: a balloon holding a drawn portrait read `うん．．．`, and
    a starburst's drawn kanji read `讖`. `_ink_is_glyph_like` requires the ink inside
    the balloon's deeper inset to **not touch the inset border** — a glyph sits with
    white around it, artwork runs to the edge (measured: 真 6% ink / no border touch;
    balloon artwork 25%, starburst 40% and a clipped caption 10% all touch). An ink
    fraction **cap (18%)** additionally excludes drawn glyphs that fill the balloon
    (the starburst measures 23%; real dialogue measures 1-11%, the reported 真 6%).
  - `_recoverable_text` keeps the result to real dialogue: a lone KANA (の, ぅ, ぁ) is
    a clipped fragment of a longer line and is rejected, a lone KANJI (真, 私, 若) is a
    word or a name and is kept, and punctuation-only marks (～～〜〜！？) stay as-is.

### Verified
- 183 unit tests pass (5 new guard tests: overlap coverage, glyph-vs-artwork border
  test, oversized-drawn-glyph cap, fragment/SFX rejection, artwork balloon skipped
  without an OCR call).
- Re-scanned job 4's 193 pages with the guards: **5** balloons recovered (pages 7, 88,
  119, 148, 182) instead of 19 — the art balloon, the starburst and the clipped-caption
  duplicate are all correctly left alone, and the reported 真 balloon still recovers.
- Post-rollout screen on the v0.27.8 outputs: the un-erased-source page list is
  byte-identical to the pre-rollout one (job 2 `[3,4,5,7,31,34,39,40,41,57,59,62,68,
  85,100,102,103,121,124]`), i.e. the re-render introduced no new garbled-source pages.

## [0.27.9] - 2026-09-16

### Fixed
- **A speech balloon holding a lone glyph was never translated.** Job-5 page 3 has a
  balloon with a single large 真 and its furigana まこと beside it; it sat in Japanese
  through every release. RT-DETR *does* find the balloon, but the text-region detector
  only fires on text *lines* — one big glyph with ruby beside it is not a line, so
  neither `text_bubble` nor `text_free` produced a region and **no OCR was ever
  attempted**. Verified by handing the worker a crop of just that balloon, upscaled
  2x: it returns the bubble and **zero** text blocks. (A second, latent trap: the
  worker discards any OCR result of a single character — `len(text.strip()) <= 1` —
  so a one-glyph balloon would have been dropped even if its region had been found.)
  `render._ocr_bubbles_without_text` closes the whole class: after the normal
  detect+OCR pass, every detected balloon that contains **no** text block has its
  interior OCR'd directly (inset crop, so the text isn't clipped and the balloon
  outline is excluded) and becomes a block when it reads as Japanese. Empty balloons
  cost no OCR call — an ink gate measured on a *deeper* inset skips them, because the
  outline curves into the corners of a shallow crop and manga-ocr will hallucinate
  kana on a blank balloon.

### Verified
- 178 unit tests pass (7 new: coverage detection, recovery, empty-balloon skip,
  no re-OCR of an already-OCR'd balloon, non-Japanese rejection, fixture guard).
- Rendered job-5 page 3 end to end: 14 -> 15 blocks, and the 真/まこと balloon is now
  lettered in English with the source and furigana erased, text centred inside the
  outline.

### Known limitation
- manga-ocr reads 真 but not the ruby まこと (at a shallow crop the furigana merges into
  garbage — it returned `真っ赤に`), so the on'yomi "SHIN" was lettered rather than the
  name "Makoto". Feeding furigana to the translator as a reading hint is a separate
  change and would fix proper nouns generally.

## [0.27.8] - 2026-09-16

### Fixed
- **Lettering overflowed the balloon it was fitted to.** v0.27.7 sized text to the
  balloon's *bounding box*, but a balloon is an oval or a spiked blob: much narrower
  at its top and bottom rows than at its waist. On real manga pages the text poked
  through the outline and ran into the neighbouring panel (job-4 pages 10/25/60 —
  `I NEARLY DIED SO MANY TIMES, THOUGH…` was lettered to the page font cap and
  printed on top of the next balloon's text). The fix targets the *shape*, not the
  box:
  - `typeset._region_avail` floods the balloon's light interior and returns, for
    every row, how wide a centred line may be before it crosses the outline
    (`2 * min(run left, run right)`, minus a margin that scales with the balloon).
    It also returns the interior's own bounding box, so the lettering is centred on
    the balloon rather than on the (looser) detection box.
  - `typeset._fit_shape` picks the largest font size whose *every* wrapped line fits
    its own row band — so a round balloon fills to its curve, while an oval or spiky
    one is never overrun. `render.py` marks the balloon/flood-fill regions
    (`shapes=`) and everything else keeps the rectangular fit.
  - Sizing falls back to the rectangle when there is no interior to measure (text
    over artwork, caption strips, slanted boxes) or when the fit finds nothing.
- **The v0.27.7 "prefer filling" rule inflated regions that have no boundary to
  respect.** Applied to a caption strip over artwork it blew a one-line caption up to
  the page font cap, colliding with neighbouring panels. That rule is removed from
  the rectangular path (v0.27.6 aesthetic sizing is back); filling is now done by the
  shape fit, which is bounded by an actual outline.
- **Free-floating vertical text sprawled across the page.** `_caption_region`
  widened a tategaki column to `1.5 x its height` (up to 60% of the page). A 54x346
  mutter column was therefore lettered across a 519px strip, over the neighbouring
  balloon. The strip is now bounded by the source column's own footprint (2.5x its
  width + 24px) so the English stays where the source text was.

### Verified
- 171 unit tests pass (7 new/updated: shape profile, per-line band fit, oval and
  spiky-balloon containment, boundary-less sizing, caption strip bound).
- A/B rendered on the isolated test instance against real pages from all three live
  jobs (Japanese manga 00010/00025/00060, Korean webtoon pages 4/40, Chinese manhua
  page 23): balloon lettering is filled *and* contained, where v0.27.7 overflowed.

## [0.27.7] - 2026-09-15

### Fixed
- **Lettering did not fill the space it had.** Two independent causes, both
  throwing away usable area, so translations sat small in the middle of roomy
  balloons (job-5 page 3 was the visible case: a 201px-wide bubble lettered at
  **9px**, and a 133x242 caption column at **10px**):
  1. **The region was inset twice.** The renderer pre-inset the parent bubble with
     `_inset_box` (15% of width, 12% of height), then `_draw_box` inset *again* by
     15% of the smaller dimension. A 201px bubble handed the text just **99px**.
     The bubble is now passed through as-is; `_draw_box` applies the single inset
     that approximates the inscribed rectangle — the same margin the caption path
     always used.
  2. **`_fit` preferred a clean wrap over filling the box.** It returns the largest
     size with at most one single-word line. That is a sensible nudge in a roomy
     box, but in a narrow region the lone-word marker fires at nearly every size, so
     the scan bottomed out below what fits. The clean size is now only preferred
     when it is not drastically smaller than the largest that fits (>= 75%).
     Measured on identical boxes, old -> new:

     | box | old | new | text |
     |---|---|---|---|
     | 93x202 | 9px | **14px** | "Twelve years ago, Munakata Kyudo Dojo" |
     | 171x257 | 15px | **23px** | "This time he collapsed just from lightly running around…" |
     | 108x158 | 11px | **16px** | "That frailness of yours-" |
     | 200x120 | 28px | 28px | roomy box — unchanged |
     | 300x200 | 35px | 35px | roomy box — unchanged |

     Roomy boxes are deliberately untouched; only genuinely narrow regions gain.
     Measured end to end on job-5 page 3 (same translations): every balloon's text
     is substantially larger, fills its white space, and still sits inside the
     outlines.
- **A stray aside found while testing:** the renderer's `_inset_box` is no longer
  called by either the ja or the ko/zh path (kept, with its unit tests, for the
  inset helper itself).

## [0.27.6] - 2026-09-15

### Fixed
- **Drawn sound effects were erased and replaced by a tiny word.** For ko/zh, any
  block with no speech bubble was lettered through `_caption_region` with the
  page-width font cap from `_draw_box` (`~width/32` = **21px on a 690px webtoon
  page**). A drawn SFX is art, not prose, so a page-wide sound effect collided
  head-on with that cap: job-2 page 85's `조~으~옹..` — 421px tall, spanning the
  width of the panel — was erased (leaving an inpainted smear) and replaced by a
  **21px** "Quiet." floating in the middle of the void.

  SFX and other short free-standing art text now letter into their **own OCR box**
  (PaddleOCR returns the tight text region for ko/zh, so the box is literally the
  footprint the sound effect occupies) with the cap derived from that box rather
  than the page, and uppercased as comics letter SFX. Same page, same block:
  21px → ~180px "QUIET.", sitting exactly where the Korean was.
  Vertical art text still uses `_caption_region` (English is horizontal, so a
  tall column genuinely needs the widening) and captions/labels are unaffected.

## [0.27.5] - 2026-09-15

### Fixed
- **Fractional box coordinates killed 10 pages.** Rescaling the boundary band
  (v0.27.4) makes the mapped-back box coordinates fractional, and downstream code
  slices numpy arrays with those boxes — `'float' object cannot be interpreted as
  an integer`. Job 2 ended `partial` with 10 of 129 pages failed. The mapping now
  rounds each corner to an integer and derives `w`/`h` from the rounded corners so
  the box cannot drift. (The job reported the failures instead of finishing quietly
  with `error=null` — that's the v0.26.0 provider-resilience work paying off.)

## [0.27.4] - 2026-09-15

### Fixed
- **The boundary band was misrecognised, silently replacing correct text with
  garbage.** v0.27.1's band is the page's last 500 px joined to the next page's
  first 500 px — a 690×1000 image. PaddleOCR's recognition is *size*-sensitive,
  not merely aspect-sensitive, and that geometry is a bad size: the same pixels
  read inside the 690×1600 page come back as nonsense from the 690×1000 join, on
  **both** the GPU worker and the CPU fallback (so it wasn't a backend issue):

  | what OCR'd it | reading of the bubble |
  |---|---|
  | page alone (690×1600) | `이` `위아래로` `전부` `사유지야.` — conf 0.93–1.00 |
  | band, raw join (690×1000) | `10` `래러이ㅎ` `이` — **garbage** |

  Because the band's reading *replaced* the page's correct one for every box in
  the band (`_band_handover`), a clean bubble became "10 LARRY, HEH." (job-2 page
  68). The band is now rescaled so its long side matches the page's long side,
  which puts the glyphs back in the size range the recognizer handles. Verified
  the straddling-bubble case the band exists for still works (job-2 page 23's
  bubble is invisible to the page pass and still reads correctly).

## [0.27.3] - 2026-09-15

### Fixed
- **Box merging snowballed into a page-swallowing block.** `_merge_stacked_lines`
  tested the vertical gap against the block's *accumulated union* box — and the
  union's height grows with every merge, so the `0.6 * min(h, bh)` threshold
  inflated and admitted ever-more-distant boxes. On job-2 page 115 four bubble
  lines plus three unrelated art misreads chained into ONE **575×1417** block
  covering most of the page; erasing it wiped the artwork, and the English landed
  across the wash. Adjacency is now tested against each **member** box, which
  bounds every merge to geometry that genuinely adjoins the bubble. Measured on
  the real page: the bubble block is now 219×220 (was 575×1417).
- **The same bug was hiding art misreads from the confidence filter.** Merging
  takes `max(conf)` of its members, so the low-confidence art misreads rode the
  bubble's 1.0 confidence and survived `_drop_low_confidence` (0.5 floor) — `'N'`
  (conf 0.395) + `'oyloo!'` (0.434) became part of a 1.0-confidence block. With
  them no longer merged in, they form their own 0.434 block and are dropped, so
  the nonsense that was lettered over the artwork is gone too.

## [0.27.2] - 2026-09-15

### Fixed
- **A boundary line was translated twice.** v0.27.1's fix OCRs the page and the
  boundary band separately, but the two passes overlap by design — so a line near
  the cut was read by BOTH. `_merge_horizontal_words` joined the pair into one
  block and the page came out reading "Live here! Live here!" (`살아! 살아!`).
  `_band_handover` now reconciles the passes: a band box that *crosses* the page
  cut is the authoritative whole-bubble read, so the page's partial view of it is
  dropped; a band box that stays inside the page and duplicates a surviving page
  box is dropped in favour of the page's own native read. Verified on the exact
  page: `숨막히는도시 생활 질리거든언제든 내려와살아!` → "City life's suffocating
  and gets old-come down and live here anytime!"

## [0.27.1] - 2026-09-15

### Fixed
- **The lookahead strip was destroying Korean/Chinese OCR.** For webtoons the
  renderer stitches the next page's top 500 px onto the page before OCR, so a
  bubble cut by the page boundary is read whole. PaddleOCR however *downsizes
  whatever image it is handed* (long side to ~960 px), so the taller stitched
  image shrank the effective glyph height and recognition collapsed into
  nonsense syllables. Measured on job-2 page 77, same pixels:

  | input | text | conf |
  |---|---|---|
  | native page | `현성아!` | 0.999 |
  | + 500 px lookahead | `야워을` | 0.609 |

  Seven clean lines came back as six garbage ones — and because the garbage
  boxes only partly covered the real text, **the Korean survived erasure while
  English was lettered over it** (the page looked like a translation failure; it
  was an OCR-input failure). The local PP-OCR fallback had the identical bug.
  Both paths now OCR the page alone at native scale, then OCR only the boundary
  *band* (page's last 500 px joined to the next page's first 500 px — 1000 px
  tall, so still near native scale) and map the band's boxes back by the band
  offset. A bubble that spans the cut is still read whole; the page's own text
  keeps its native resolution.
- **One bubble now holds one string.** A multi-line ko/zh bubble can come back
  as several OCR blocks; each resolved to the same parent bubble and the
  typesetter centred *every* one of them into it, lettering English on top of
  English (job-2 page 34: `CAN YOU HEAR ME?` drawn straight through
  `L-SENBAE! SENBAE!`). Blocks sharing a parent bubble are now merged before
  translation — which also gives the LLM the whole bubble as one unit instead of
  half a line at a time. Free-floating text (stat columns, captions) has no
  bubble and is never merged.

## [0.27.0] - 2026-09-15

### Fixed
- **Auto language detection read a KOREAN job as Chinese and silently destroyed
  its translations.** `detect_language` treated "the `ch` recognizer produced
  hanzi" as proof of Chinese — but the `ch` recognizer happily reads Korean as
  plausible hanzi, so a Korean manhwa auto-detected as `zh`. The Chinese
  recognizer then finds nothing in hangul, every page rendered **0 blocks**, and
  the page was written back out as the *untranslated original*. Resuming/re-rendering
  job 2 overwrote 8 already-translated pages before it was caught. Detection now
  treats only KANA and HANGUL as positive script markers and lets hangul beat
  hanzi, with `>= 2` character floors so a single stray glyph can never decide
  (one katakana amid garbage was flipping Korean pages toward Japanese). Measured
  on the real pages — job2 p1: ko probe hangul 20 / hanzi 0; job2 p2: ko hangul 16;
  job3 (Chinese) p1: ko probe hangul 0. Verified after the fix: job 2 → ko,
  job 3 → zh, job 4 → ja.
- **A re-render that finds nothing now says so.** If a page renders 0 blocks but
  previously had some, the engine logs an ERROR naming the page (it has just been
  overwritten with the untranslated original) instead of finishing silently
  "done" — the failure mode above was invisible until the pages were inspected.
- **English lettered over un-erased source text (the "I'M RE하고" defect).** A
  page-boundary carryover patch could stamp the page's OWN source glyphs back on
  top of its freshly lettered bubble. Two compounding faults: (1) a page never
  erased the next page's text that the 500 px lookahead strip had pulled in, so
  the extracted patch contained that un-erased source text; (2) the previous
  page's target region could bleed across the boundary and clip a bubble this
  page owns, so the patch landed on top of its new lettering. Fixed by erasing
  lookahead-strip text (it is never lettered there anyway) and dropping any
  carryover patch that overlaps text the current page owns (~45% clippage, which
  the old >50% containment test ignored). Verified on job-2 pages 41 and 77, and
  the legitimate straddle (page 24) still carries over correctly.
- **Blank carryover patches no longer damage the next page.** A straddling region
  whose English sat higher up produced a patch of bare inpainted background (no
  lettering). Pasting it achieved nothing, while its erase box destroyed the next
  page's own content — job-2 page 30's `그날` narration was half-erased into a
  smudge. Patches without lettering contrast are now dropped (measured: blank
  10.8 std vs real patches 49 / 72).
- **Microscopic English on tall-narrow vertical Japanese text.** A tategaki
  narration column over artwork (`根性はあるけど器用貧乏！？`, job-4 page 5) had a
  long English line fitted into its ~1-glyph width, producing unreadable
  lettering. The "English is horizontal, so only tall-narrow text gets a wide
  strip" rule already used by the Korean/Chinese path now applies to Japanese
  free-floating text too.

### Added
- **Resume now works on a "partial" job.** `POST /api/jobs/{id}/start` previously
  only accepted paused/cancelled/failed, so a job that finished `partial` (some
  pages failed — e.g. a provider outage) could not be resumed at all, making the
  "then Resume" advice in the new provider-outage error message impossible to
  follow. It now re-queues the job and resets any page left running/failed, while
  every page already `done` is skipped — a Resume never re-renders finished pages.
- **Re-render a single page** (`POST /api/jobs/{id}/pages/{index}/rerender`, with a
  **Re-render page** button in the side-by-side viewer). Resets just that page and
  re-queues the job, so a pipeline fix can be applied to pages produced by older
  code without re-running the whole job. Job counters (`pages_done`,
  `blocks_found`, `blocks_ok`) have the page's previous contribution subtracted
  first so they stay honest.

## [0.26.0] - 2026-09-15

### Added
- **Translation timeouts and retries are now configurable in Settings** (Settings → Translation provider):
  - `Request timeout (seconds)` — default **300** (lax). Was hardcoded at 120 s.
  - `Retries per request` — default **2**, retried on a transient error with increasing backoff.
  - `Stop job after N consecutive failed pages` — default **3**.

  The defaults are deliberately lax: a degraded provider can answer slowly, and
  aborting a slow-but-alive call throws the whole page away.
- **Provider unavailability is reported instead of looking like a stall.** Every
  translation failure is now a typed `ProviderError` naming the endpoint — e.g.
  `api.deepseek.com returned no completion for model 'deepseek-flash'
  (empty/absent 'choices')` — instead of surfacing as a bare
  `KeyError: 'choices'` or `'NoneType' object is not subscriptable`. Transient
  errors (timeout, connection failure, HTTP 429/5xx, empty completion) are
  retried and logged; permanent ones (HTTP 401/400) fail immediately.
- **Jobs no longer finish silently.** If the provider stays down, the job stops
  with the reason shown on its card (`Translation provider unavailable — 3 pages
  failed in a row (...)`) rather than running to the end leaving pages
  untranslated — Resume re-runs only the unfinished pages. A job that finishes
  with failed pages always carries an error (e.g. `1 of 193 page(s) failed —
  first error (page 4): ...`) instead of showing a bare `partial`.

## [0.25.0] - 2026-09-12

### Added
- **Slanted speech-bubble text now rotates to match the bubble's tilt.** Webtoon/manhua bubbles are often drawn at an angle; the pipeline previously flattened OCR quads to axis-aligned boxes and lettered English dead-horizontal. A new `TextBlock.angle` field + `region_angle()` (`cv2.minAreaRect` on the bubble's fill shape) computes the tilt, and the typesetter rotates the text to match (positive = down-to-right).
- **Webtoon page-boundary lookahead + carryover.** A bubble straddling two vertical-scroll cuts is now read whole (next page's top ~500px stitched on) and its below-cut half carried over, so English stays continuous when re-stacked.

### Fixed
- **Multi-line Korean/Chinese dialogue silently dropped.** `_drop_titles` (a Japanese title-art heuristic) matched tall multi-line ko/zh speech bubbles and threw them away; now gated to `lang == "ja"`. Recovered 17 blocks across 8 pages.
- **Overlapping OCR lines from tilted bubbles double-typeset.** Same-line fragments now merge instead of stacking two translations on top of each other.
- **Korean spaced lines split into word boxes** (`좋지 않아?` → `좋지` + `않아?`) are merged left-to-right before translation.
- **Stale `text_blocks` accumulated on re-render** — now cleared before persisting, so the viewer shows no duplicates.
- **Stale page images after re-render** — cache-busting headers (`Cache-Control: no-store`) on page endpoints.

## [0.24.9] - 2026-09-10

### Fixed
- **Misread SFX / decorative glyphs transliterated as nonsense.** PaddleOCR reads real dialogue at ~0.9-1.0 confidence but garbles stylized SFX and decorative hanzi at <0.5 (e.g. 阿大奥色狂→"Ah Da Ao Se Kuang", 福→"Blessing", a garbled sign→"Fang Yu Yuan Yuan Si Min"). Those low-confidence boxes are now dropped so the original artwork stays untouched. Japanese is unaffected (manga-ocr emits no confidence score, so the gate never sees it); Korean legit text reads ≥0.51, safely above the 0.5 threshold.

## [0.24.8] - 2026-09-10

### Fixed
- **Chinese/Korean text misaligned — bubbles found by fragile colour flood-fill.** The ko/zh path recovered each speech box with a white/coloured flood-fill (`find_speech_box`), which leaked or returned None for spiked/coloured/anti-aliased bubbles, so English lettering overflowed the bubble, shrank to a tight OCR box, or drifted off-centre. It now runs the SAME RT-DETR bubble detector the ja path uses (`ogkalu/comic-text-and-bubble-detector`, language-agnostic) and assigns each OCR line to its bubble via `find_parent_bubble`, then letters into the bubble's inscribed rectangle. The flood-fill remains only as a fallback for bubbles the detector misses.

## [0.24.7] - 2026-09-10

### Fixed
- **Separate manhua/webtoon bubbles merged into one giant block** — `_merge_stacked_lines` judged the inter-fragment gap against the *accumulated* block height (`0.6 * max(h, bh)`), so once a bubble grew tall, its gap threshold grew with it and it greedily absorbed the next vertically-stacked bubble below. Three separate bubbles became one enormous floating text block spanning the panel. The gap is now judged against the *fragment* height (`0.6 * min(h, bh)`), so a real inter-bubble gap (well over half a line height) splits correctly while wrapped lines within one bubble still merge.
- **Short vertical labels blew up to a page-wide strip** — a 2-char vertical label (大吉) with no enclosing speech box was lettered across a fixed 60%-page strip, typeset at an enormous font. The vertical-caption strip width now tracks the source text's length (≈ its vertical height), capped at 60% page.

## [0.24.6] - 2026-09-09

### Fixed
- **Size floor was dropping real small text** — the v0.24.5 foliage filter dropped *any* box under ~55px in both dimensions, which also caught legitimate single characters and small SFX (嗝 at 0.949, a lone 这 at 1.000). The filter now requires **small AND low-confidence** (rec conf < 0.9): foliage false positives (业 0.707, 义 0.860) are still dropped, but small high-confidence text survives.
- **Free-floating text widened too far** — lettering *every* boxless text across 60% page width spilled on-screen UI labels (连心台已升起, 牵手成功) and single characters into neighbouring panels. Only tall-narrow (vertical) captions now get the wide strip; wide footnotes and small labels keep their own width and just gain height for wrapped lines.

## [0.24.5] - 2026-09-09

### Fixed
- **Foliage/texture misread as text** — dense artwork (tree canopies, grass, clouds) produced tiny false-positive detections that PaddleOCR read as single hanzi at moderate confidence (e.g. `业`→"KARMA", `义`→"RIGHTEOUSNESS" rendered over the art). A minimum-size floor now drops any detected box smaller than ~55px in *both* dimensions (real text — even single-char SFX like `啊` — has one dimension above it), applied to both the local OCR path and the GPU-worker path.
- **Publisher watermarks "translated"** — a Chinese manhua's corner watermark (`腾讯动漫`) is hanzi, so the ASCII-watermark skip missed it and it was lettered as "Tencent Comics" over the logo. Text sitting in the bottom ~6% of the page and hugging either edge is now skipped like other watermarks.
- **Free-floating captions too small** — a wide footnote or a tall vertical caption (问世间情为何物) with no enclosing speech box was fitted to the *source text's* box shape (short-wide / tall-narrow), so English — which is horizontal — was crushed to a tiny font. Free-floating text is now lettered across a generous horizontal strip centered on the original, and horizontal text is capped at ~0.8× its own height so a short footnote no longer blows up to the full-page cap.

## [0.24.4] - 2026-09-08

### Fixed
- **Overflow from under-recovered speech boxes** — the colour flood-fill could stop short (truncated at a panel edge / anti-aliased outline) and return a box *shorter* than the text it was supposed to contain, so lettering sized to that box overflowed the real bubble. `find_speech_box` now rejects any recovered box smaller than the text in either dimension and falls back to OCR-box expansion.

## [0.24.3] - 2026-09-08

### Fixed
- **Over-shrunk text in wide bubbles** — the clean-size preference required *zero* single-word lines, so a long caption whose last line is a single word was shrunk far more than needed, leaving a large bubble half-empty. It now allows a single lone-word (orphan) last line, so wide bubbles keep a readable size.
- **Split narration blocks** — a multi-line narration whose OCR line boxes overlap by ~20–30% (box padding) was left as two/three separate blocks (e.g. "Even though that's what" + "the profile says…" floating apart), because the merge rejected overlap above 0.3×box-height — right at the boundary of normal OCR padding. The overlap tolerance is now 0.6×height, so adjacent fragments merge into one coherent block.

## [0.24.2] - 2026-09-08

### Fixed
- **Chopped single-word line breaks in narrow bubbles** — the typesetter sized text to the *largest* font that fit the box, which in a narrow bubble left each word on its own line ("I / WAITED / IN LINE / FOR / …"). The wrapper is now a minimum-raggedness dynamic program (balanced lines, no dangling lone word), and the sizer prefers the largest size whose wrapped lines carry *no* single-word line — so a tall-narrow bubble now reads "I WAITED / IN LINE FOR / TWO DAYS / AND NIGHTS" instead of eight one-word lines.

## [0.24.1] - 2026-09-08

### Fixed
- **Korean/Chinese text overflowing its speech box** — the ko/zh path lettered into the full recovered bubble bounding box with no inset, so English was sized to the box *including* the outline and starburst spikes and spilled over the drawn bubble. The recovered box is now inset (15% width / 12% height) to its inscribed rectangle — the same inset the Japanese path already applied.
- **Text too small when no speech box was recovered** — when the colour-aware flood-fill leaked into the white page background (thin/anti-aliased bubble outlines) and returned no box, the typesetter fell back to the tight OCR box and lettered tiny. It now expands the OCR box a modest fraction toward the enclosing bubble instead.

### Changed
- **Translation prompt keeps a character's name separate from their line** — the model was fusing the speaker's name with the following contraction ("Meng Erfei-you've…"). The prompt now explicitly instructs a comma-and-space separation ("Meng Erfei, you've…"), fixing the awkward hyphenated name breaks.

## [0.24.0] - 2026-09-07

### Changed
- **Korean/Chinese dialogue is now lettered per speech box, not per OCR line** — a multi-line speech box comes back from OCR as one box per line, so the ko/zh path now merges vertically-stacked line fragments into a single block and recovers the enclosing speech box (white *or* flat-coloured, via a new colour-aware flood-fill), lettering the whole bubble as one unit. Fixes the "text too small / cramped in dead space" problem.
- **Sentence-aware line wrapping** — the typesetter now prefers to break lines after sentence-ending punctuation (`. ! ?`) and balances line lengths, so long dialogue no longer splits mid-phrase or dangles a lone word.

### Fixed
- **CJK ellipsis collapse** — "……" was rendering as a literal six dots ("......"); runs of 3+ dots now collapse to a single "..." in translated text.

## [0.23.1] - 2026-09-06

### Added
- **Source language selector in Settings** — the `source_lang` setting (`auto`/`ja`/`ko`/`zh`, added in v0.20.0 but backend-only) is now exposed in Settings → General, so a user can force a language when auto-detect picks the wrong script.

## [0.23.0] - 2026-09-06

### Fixed
- **`source_lang=auto` hang on dense manhua pages** — auto-detection probed the `korean` recognizer first, whose language-agnostic detection floods a dense Chinese/Japanese page with hundreds of false-positive boxes and wedges the memory-constrained host. Detection now probes `ch` first (it reads hanzi AND kana, classifying both CJK scripts in one pass) and only falls back to the `korean` recognizer when `ch` finds no CJK — so the korean probe never runs on a Chinese/Japanese page.

### Added
- **90°-rotation fallback for vertical Korean/Chinese text** — a tall-narrow box read below 0.5 confidence (the textline-orientation classifier missed a vertical line) is now re-read after rotating its crop 90° so the recognizer sees it horizontally. The better read wins, mapped back to the original box. Applied in both the app's `read_boxes_text` and the GPU worker's `ocr_multilingual_blocks` (kept in lockstep).
- **Korean (manhwa/webtoon) translation prompt** — a dedicated system prompt preserves the banmal (casual) vs jondaenmal (formal/honorific) register, slang, playful banter, and emotional outbursts that the generic Japanese-shaped prompt flattened into stiff English.

### Changed
- GPU worker bumped to **0.3.1** (mirrors the vertical-text rotation fallback).

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
- **Streaming upload ingest (OOM)** — `ingest_upload` no longer reads whole files into RAM (`f.file.read()` / `io.BytesIO(...)`). Uploads and CBZ/ZIP archives are staged to disk and expanded in 1 MB chunks, so a large archive can't OOM-kill the container on the swap-less VM.
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
