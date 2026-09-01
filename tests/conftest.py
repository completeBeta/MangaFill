"""Test setup — point the app at a throwaway DB + dirs before any app import.

`app.config.settings` is read at import time, so these env vars MUST be set before
any test module imports `app` (pytest imports conftest first). The test dir is wiped
each session so runs don't leak state into each other.
"""
import os
import shutil

_TMP = "/tmp/mangafill-test"
shutil.rmtree(_TMP, ignore_errors=True)

os.environ["MANGA_FILL_STATE_DB"] = f"{_TMP}/test.db"
os.environ["MANGA_FILL_JOBS_DIR"] = f"{_TMP}/jobs"
os.environ["MANGA_FILL_RAW_DIR"] = f"{_TMP}/input"
os.environ["MANGA_FILL_OUTPUT_DIR"] = f"{_TMP}/output"
