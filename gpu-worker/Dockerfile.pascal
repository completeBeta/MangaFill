# Manga Fill GPU worker — NVIDIA Pascal (P2000 / GTX 10-series) variant.
#
# The legacy NVIDIA image, kept ONLY for Pascal (sm_61) cards like the P2000.
# PyTorch 2.6+ dropped Pascal, so this pins torch 2.5.1 + CUDA 12.4 — the last
# line that still ships sm_61 kernels. Everyone on a newer card (RTX 20/30/40/50)
# should use the default `Dockerfile` instead: same code, newer torch.
#
# On torch 2.5.1 the `models.py` pickle-safety lift is ACTIVE (guarded by a
# torch<2.6 check) — required because transformers 5.x hard-blocks pickle
# checkpoints below torch 2.6.
FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/root/.cache/huggingface

WORKDIR /app

# libgomp1 for numpy OpenMP; libglib2.0-0 for opencv-python-headless (import cv2).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# deps first (cached separately from the code COPY)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --no-deps simple-lama-inpainting \
    && (pip uninstall -y opencv-python opencv-contrib-python 2>/dev/null || true) \
    && pip install --no-cache-dir --force-reinstall --no-deps "opencv-python-headless>=4.8,<5.0"

COPY main.py models.py ./

# manga-ocr tokenizer patch (mirrors the app Dockerfile).
RUN python -c "import manga_ocr, os; p=os.path.join(os.path.dirname(manga_ocr.__file__),'ocr.py'); s=open(p).read(); s=s.replace('AutoTokenizer.from_pretrained(pretrained_model_name_or_path)', 'AutoTokenizer.from_pretrained(pretrained_model_name_or_path, tokenizer_type=\"bert-japanese\")'); open(p,'w').write(s)"

# Bake the model weights in at build time.
RUN python -c "from models import warmup; warmup()"

EXPOSE 9001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9001", "--workers", "1"]
