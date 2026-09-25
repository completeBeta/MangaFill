"""Replay bundles — capture exactly what the typesetter was handed, per page.

WHY. A re-render re-translates, so two renders of the same page differ by WORDING as
much as by code, and the whole-library A/B harness (`scripts/ab_library.py`) can only
triage: its first full run came out SIZE-DROP 123 / SIZE-GROW 120, symmetric churn.
To gate a typesetter change you need the same inputs twice. This module records, per
page, the exact inputs of the typeset stage — the INPAINTED page (which exists nowhere
on disk otherwise), the ORIGINAL's gray, and every block/region/shape decision — so
`scripts/replay_typeset.py` can render those inputs through two builds of the app and
diff the pixels. The only variable left is the code.

OFF UNLESS OPTED IN, same sentinel pattern as `fitlog`::

    docker exec <container> mkdir -p /tmp/mf_replay
    # ...render / POST /api/jobs/<id>/pages/<i>/rerender...
    docker cp <container>:/tmp/mf_replay ./replay

Storage is ~1 MB/page (RGB inpaint + gray original), so pull and clear it between runs.
"""
from __future__ import annotations

import json
import os
import re
import sys

import numpy as np
from PIL import Image

DIR = "/tmp/mf_replay"


def enabled() -> bool:
    """True when this container was opted in. Never raises."""
    try:
        return os.path.isdir(DIR)
    except Exception:
        return False


def stem_for(image_path: str) -> str:
    """`j<job>_<page>` — the JOB id is part of the bundle's name.

    Page filenames are NOT unique across jobs: the ko webtoon and the zh manhua both name
    their pages `page_007.jpg`, so a bare stem made one job's bundle silently overwrite the
    other's — a JSON describing one page with the PNGs of another, which is worse than no
    bundle at all (it poisons the A/B exactly where it looks most confident).
    """
    base = os.path.basename(image_path)
    stem = base.rsplit(".", 1)[0]
    m = re.search(r"/jobs/(\d+)/", image_path.replace("\\", "/"))
    return f"j{m.group(1)}_{stem}" if m else stem


def dump(image_path: str, inpainted: Image.Image, original_gray, blocks: list,
         targets: list, shaped: set, font_id: str | None) -> None:
    """Write `<stem>.json` + `<stem>_inpaint.png` + `<stem>_orig.png` for one page.

    `targets` is the final `(block, region)` list (after `_partition_shared_bubble`),
    `shaped` the ids whose region came from a balloon/box outline, `inpainted` the page
    ABOUT to be typeset. Instrumentation must never be able to break a render, so every
    failure here is swallowed and reported on stderr only.
    """
    try:
        if not enabled():
            return
        os.makedirs(DIR, exist_ok=True)
        stem = stem_for(image_path)
        idx = {id(b): i for i, b in enumerate(blocks)}
        regions: dict[str, list] = {}
        only: list[int] = []
        for b, region in targets:
            i = idx.get(id(b))
            if i is None or region is None:
                continue
            regions[str(i)] = [int(v) for v in region]
            only.append(i)
        rows = [
            {
                "i": i,
                "bbox": [int(v) for v in b.bbox],
                "en": b.translation or "",
                "jp": b.text or "",
                "orient": b.orientation,
                "angle": float(getattr(b, "angle", 0.0) or 0.0),
                "sfx": bool(getattr(b, "is_sfx", False)),
            }
            for i, b in enumerate(blocks)
        ]
        p_inp = os.path.join(DIR, stem + "_inpaint.png")
        if not os.path.exists(p_inp):
            inpainted.save(p_inp)
        p_org = os.path.join(DIR, stem + "_orig.png")
        if original_gray is not None and not os.path.exists(p_org):
            arr = original_gray
            if not hasattr(arr, "shape"):
                arr = np.asarray(arr)
            if arr.ndim == 3:
                arr = arr[..., 0]
            Image.fromarray(arr.astype("uint8")).save(p_org)
        p_json = os.path.join(DIR, stem + ".json")
        with open(p_json, "w") as fh:
            json.dump(
                {
                    "page": stem,
                    "w": int(inpainted.width),
                    "h": int(inpainted.height),
                    "font_id": font_id,
                    "blocks": rows,
                    "regions": regions,
                    "only": only,
                    "shaped": [i for i, (b, _r) in enumerate(targets)
                               if id(b) in shaped and id(b) in idx],
                },
                fh,
            )
    except Exception as exc:  # noqa: BLE001 — diagnostics must never break a render
        print(f"[replay] dump failed for {image_path}: {exc!r}", file=sys.stderr)
