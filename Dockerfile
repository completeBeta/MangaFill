# CPU-first image (Phase 1). GPU acceleration is wired later as a remote
# worker on the GPU host — never in this container.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Native deps for OpenCV/Pillow + curl (font pull) + DejaVu fonts (lettering
# fallback when the manga font can't be pulled; slim base ships no fonts).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        curl \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Manga lettering font — Anime Ace (Blambot). Its license (freeware for
# non-profit use, NO redistribution) forbids committing the .ttf to the repo,
# so it's pulled at build time — the same pattern as the model weights (which
# come from HuggingFace, not from git). Best-effort: if the fetch fails the
# build still succeeds and typeset falls back to DejaVu Sans Bold. A font
# mounted at /app/fonts still takes precedence via $MANGA_FILL_FONT / the
# resolution order in app/pipeline/typeset.py.
RUN mkdir -p /app/fonts \
    && ( curl -fsSL --max-time 60 -o /app/fonts/AnimeAce-Regular.ttf \
            "https://st.1001fonts.net/download/font/anime-ace.regular.ttf" \
         && curl -fsSL --max-time 60 -o /app/fonts/AnimeAce-LICENSE.txt \
            "https://st.1001fonts.net/license/anime-ace/font%20info.txt" \
         || echo "WARN: Anime Ace pull failed — typeset will use DejaVu Sans Bold" )

# ---- ML deps first (cached separately from the code COPY) -----------------
# Torch must be CPU-only (PyPI's default pulls a ~2.5 GB CUDA build that can't
# run here). Then the ML requirements, then simple-lama-inpainting with
# --no-deps (its stale `pillow<10` + `numpy<2` pins conflict with manga-ocr).
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir "numpy<2.0" \
    && pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu
COPY requirements-ml.txt .
RUN pip install --no-cache-dir -r requirements-ml.txt \
    && pip install --no-cache-dir --no-deps simple-lama-inpainting

# ---- code (this layer changes every deploy; the heavy deps above stay cached)
COPY . .
RUN pip install --no-cache-dir .

# manga-ocr 0.1.16 predates transformers>=5.13, which misdetects its tokenizer
# class for VisionEncoderDecoderModel and falls back to an incompatible
# fast-only backend. Force the bert-japanese tokenizer. (Idempotent.)
RUN python -c "import manga_ocr, os; p=os.path.join(os.path.dirname(manga_ocr.__file__),'ocr.py'); s=open(p).read(); s=s.replace('AutoTokenizer.from_pretrained(pretrained_model_name_or_path)', 'AutoTokenizer.from_pretrained(pretrained_model_name_or_path, tokenizer_type=\"bert-japanese\")'); open(p,'w').write(s)"

EXPOSE 8788

# Single worker — all state is in SQLite, not process memory (Subber lesson).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8788", "--workers", "1"]
