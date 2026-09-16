"""Detector floor for halftone-screentone false positives (v0.27.13 / worker 0.3.2).

A narrow-and-tall `text_free` region whose detector score is weak is halftone, not
text: manga-ocr reads a plausible phrase out of the dots and the app letters it onto
the artwork (job-4 page 118 got five overlapping copies of one line). The numbers come
from measuring nine such pages — every real case below is a box that must SURVIVE.

Mirrored in gpu-worker/models.py; the two implementations must agree.
"""
from app.pipeline import detector
from app.pipeline.detector import (
    TEXT_FREE_TALL_MAX_W,
    TEXT_FREE_TALL_MIN_H,
    TEXT_FREE_TALL_MIN_SCORE,
    is_halftone_prone_text_free,
)


# --- the phantoms (real detections, verified at 5x zoom to be pure screentone) ---

def test_measured_screentone_detections_are_filtered():
    for w, h, score in [(14, 104, 0.202), (23, 416, 0.430), (30, 390, 0.308),
                        (19, 378, 0.228), (37, 240, 0.289), (18, 123, 0.312),
                        (22, 379, 0.287), (21, 108, 0.227), (22, 81, 0.231),
                        (29, 95, 0.265), (23, 89, 0.265), (44, 190, 0.289)]:
        assert is_halftone_prone_text_free(w, h, score), (w, h, score)


# --- real free text that must survive (scores/sizes from the same measurement) ---

def test_measured_real_free_text_is_kept():
    for w, h, score, why in [
        (34, 130, 0.451, "chart caption, just above the floor"),
        (51, 154, 0.253, "drawn SFX, wider than a phantom column"),
        (51, 239, 0.231, "mutter column, 51px wide"),
        (44, 43, 0.249, "tiny SFX, too short to be a column"),
        (108, 111, 0.218, "wide mutter line"),
        (160, 146, 0.494, "wide drawn glyph"),
        (140, 281, 0.053, "large art region"),
        (31, 76, 0.200, "real うん in a bubble, h just under the bar"),
        (26, 79, 0.230, "short column (h < 80)"),
    ]:
        assert not is_halftone_prone_text_free(w, h, score), why


def test_boundaries_are_exclusive_at_the_floor():
    assert is_halftone_prone_text_free(48, 80, 0.439)
    assert not is_halftone_prone_text_free(48, 80, 0.440)   # score == floor passes
    assert not is_halftone_prone_text_free(49, 80, 0.100)   # 1px too wide
    assert not is_halftone_prone_text_free(48, 79, 0.100)   # 1px too short


def test_missing_score_never_filters():
    assert not is_halftone_prone_text_free(20, 300, None)


def test_constants_are_the_calibrated_ones():
    assert (TEXT_FREE_TALL_MIN_SCORE, TEXT_FREE_TALL_MAX_W, TEXT_FREE_TALL_MIN_H) == (0.44, 48, 80)


# --- wiring: detect_containers drops them and counts them ----------------------

class _FakeImage:
    size = (1125, 1600)


class _FakeProcessor:
    def __call__(self, images=None, return_tensors=None):
        return {}

    def post_process_object_detection(self, outputs, target_sizes=None, threshold=0.2):
        return [{
            "labels": [2, 2, 0, 1],
            "boxes": [[100, 100, 114, 204],      # text_free, 14x104, score 0.20 -> phantom
                      [300, 300, 334, 430],      # text_free, 34x130, score 0.60 -> real
                      [500, 500, 700, 800],      # bubble
                      [800, 800, 850, 850]],     # text_bubble
            "scores": [0.20, 0.60, 0.97, 0.95],
        }]


def test_detect_containers_filters_and_counts(monkeypatch):
    monkeypatch.setattr(detector, "_get", lambda: (lambda **kw: None, _FakeProcessor()))
    monkeypatch.setattr(detector, "get_device", lambda: "cpu")
    out = detector.detect_containers(_FakeImage())
    assert out["text_free"] == [(300, 300, 34, 130)]
    assert out["dropped_halftone"] == 1
    # bubbles / text_bubble are never filtered by this rule
    assert out["bubble"] == [(500, 500, 200, 300)]
    assert out["text_bubble"] == [(800, 800, 50, 50)]
