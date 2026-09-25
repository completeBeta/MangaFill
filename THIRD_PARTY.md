# Third-party notices

Manga Fill is distributed under the **GNU Affero General Public License v3.0**
(`LICENSE`). It includes code and content from other projects, listed here with
their own licences. Nothing in this file changes the licence of a component.

---

## Vendored source code

### manga-image-translator — GPL-3.0

| | |
|---|---|
| Upstream | https://github.com/zyddnys/manga-image-translator |
| Commit | `95227a2bb0fd306cd4f0c104d57284026f991b3a` (branch `main`, 2026-07-20) |
| Licence | GNU General Public License v3.0 — full text in [`LICENSES/GPL-3.0.txt`](LICENSES/GPL-3.0.txt) |
| Copyright | the manga-image-translator contributors |

Files vendored into this repository (copied verbatim; each carries a header with its
upstream path, the pinned commit and the SHA-256 of the upstream body):

| File here | Upstream path | Why |
|---|---|---|
| `app/vendor/manga_image_translator/ballon_extractor.py` | `manga_translator/rendering/ballon_extractor.py` | Per-block speech-balloon interior from a local window — the two-touching-balloons case |

**How these two licences combine.** AGPL-3.0 section 13 states that the licensee
"ha[s] permission to link or combine any covered work with a work licensed under
version 3 of the GNU General Public License into a single combined work, and to
convey the resulting work. The terms of this License will continue to apply to the
part which is the covered work, but the work with which it is combined will remain
governed by version 3 of the GNU General Public License."

In practice, for this repository:

* Manga Fill's own code is licensed **AGPL-3.0** (see `LICENSE`).
* The files under `app/vendor/manga_image_translator/` are **GPL-3.0** and stay
  GPL-3.0. They keep their notices, and this notice travels with them if they are
  ever taken out of this project.
* We do **not** relicense upstream code. If you redistribute a modified version of a
  vendored file, the GPL-3.0 obligations (source availability, notices, the same
  licence) apply to that file.

**Source offer (AGPL-3.0 §13).** Running the application as a network service obliges
the operator to offer its Corresponding Source to users of that service. This
repository *is* that source: the application's About/README points back here, and the
vendored GPL-3.0 files can be fetched from the upstream commit above.

---

## Fonts

| Font | Licence | Notes |
|---|---|---|
| Comic Shanns (`ComicShanns-Regular.ttf`) | **MIT** (© 2020 Shannon Miwa) — see `fonts/MIT-comicshanns.txt` | **shipped in this repo** (MIT permits redistribution with the notice). The same face is the upstream engine's default English lettering font. Selectable in Settings → Fonts |
| Comic Neue, Bangers, Patrick Hand, Gloria Hallelujah | SIL OFL 1.1 (texts in `fonts/OFL-*.txt`) | shipped in this repo |
| DejaVu Sans Bold | Bitstream Vera / DejaVu (permissive) | shipped by the base image (`fonts-dejavu-core`) |
| Anime Ace (Blambot) | freeware, non-profit use, **no redistribution** | **not** committed here — pulled at Docker build time; if the pull fails, lettering falls back to DejaVu Sans Bold |
| Noto Sans Mono CJK / Arial Unicode / MS Gothic / MS YaHei (present in upstream's `fonts/`) | OFL (Noto) for Noto Sans Mono CJK; **proprietary** for Arial Unicode, MS Gothic and MS YaHei | **not** shipped here. Only Comic Shanns (MIT) qualified for redistribution, so only it was taken |

---

## Python dependencies

Runtime dependencies are installed from PyPI (see `pyproject.toml` and
`requirements-ml.txt`) and are **not** vendored here. Notable ones:
`torch`/`torchvision` (BSD-3-Clause), `manga-ocr` (Apache-2.0), `paddleocr`/
`paddlex` (Apache-2.0), `simple-lama-inpainting` (Apache-2.0), `fastapi` (MIT),
`pillow` (HPND), `opencv-python` (Apache-2.0), `numpy` (BSD-3-Clause).

Model **weights** are downloaded at runtime (HuggingFace and the OCR packages) and
are not part of this repository.
