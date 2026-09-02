# Manga Fill GPU worker — runs the vision models (RT-DETR detect, manga-ocr,
# LaMa inpaint) on an NVIDIA GPU and exposes them over HTTP for the app.
#
# Base image pins PyTorch 2.5.1 + CUDA 12.4: the last line that still ships
# sm_61 (Pascal) kernels. The user's GPU is a P2000 (Pascal, 5 GB) — PyTorch 2.6+
# dropped Pascal, so this must NOT be bumped past 2.5.x.
FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/root/.cache/huggingface

WORKDIR /app

# libgomp1 for numpy OpenMP. (No libgl1 — the worker doesn't use OpenCV, and
# libgl1 would drag in the whole Mesa/LLVM stack for nothing.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# deps first (cached separately from the code COPY)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --no-deps simple-lama-inpainting

COPY main.py models.py ./

# manga-ocr 0.1.16 predates the newer transformers tokenizer auto-detection, which
# misclassifies its VisionEncoderDecoder tokenizer and falls back to an incompatible
# fast-only backend. Force the bert-japanese tokenizer. (Mirrors the app's Dockerfile;
# idempotent.)
RUN python -c "import manga_ocr, os; p=os.path.join(os.path.dirname(manga_ocr.__file__),'ocr.py'); s=open(p).read(); s=s.replace('AutoTokenizer.from_pretrained(pretrained_model_name_or_path)', 'AutoTokenizer.from_pretrained(pretrained_model_name_or_path, tokenizer_type=\"bert-japanese\")'); open(p,'w').write(s)"

# Bake the model weights in at build time so the first request is instant and the
# container needs no runtime network access to HuggingFace.
RUN python -c "from models import warmup; warmup()"

EXPOSE 9001

# Single worker — model weights live in process memory, not a shared store.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9001", "--workers", "1"]
