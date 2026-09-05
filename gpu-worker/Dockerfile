# Manga Fill GPU worker — NVIDIA (RTX 20/30/40/50, the modern default).
#
# The default NVIDIA image. Covers Turing through Blackwell: RTX 20-series
# (sm_75), RTX 30 (sm_86), RTX 40 (sm_89), and RTX 50 (sm_120). PyTorch 2.7.1 +
# CUDA 12.8 is the first line with sm_120 (Blackwell) kernels — RTX 50 needs it.
#
# Pascal (P2000 / GTX 10-series, sm_61) is NOT in this image — PyTorch dropped
# Pascal in 2.6. Use `Dockerfile.pascal` for those cards.
#
# `models.py` handles torch 2.6+ natively (the pickle-safety lift is a no-op
# here; manga-ocr's checkpoint is a pure-tensor state_dict that loads fine under
# weights_only=True).
FROM pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/root/.cache/huggingface

WORKDIR /app

# libgomp1 for numpy OpenMP; libglib2.0-0 for opencv-python-headless (import cv2).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        libglib2.0-0 \
        libgl1 \
    && rm -rf /var/lib/apt/lists/*

# deps first (cached separately from the code COPY)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --no-deps simple-lama-inpainting

COPY main.py models.py ./

# manga-ocr tokenizer patch (mirrors the app Dockerfile).
RUN python -c "import manga_ocr, os; p=os.path.join(os.path.dirname(manga_ocr.__file__),'ocr.py'); s=open(p).read(); s=s.replace('AutoTokenizer.from_pretrained(pretrained_model_name_or_path)', 'AutoTokenizer.from_pretrained(pretrained_model_name_or_path, tokenizer_type=\"bert-japanese\")'); open(p,'w').write(s)"

# Bake the model weights in at build time.
RUN python -c "from models import warmup; warmup()"

EXPOSE 9001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9001", "--workers", "1"]
