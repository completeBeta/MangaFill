# syntax=docker/dockerfile:1
# ============================================================================
# Manga Fill ENGINE — upstream manga-image-translator, unmodified.
#
#   Upstream : https://github.com/zyddnys/manga-image-translator  (GPL-3.0)
#   Commit   : 95227a2bb0fd306cd4f0c104d57284026f991b3a
#   Licence  : GPL-3.0 — see ../LICENSES/GPL-3.0.txt and ../THIRD_PARTY.md
#
# ONE IMAGE PER ACCELERATOR TYPE (house rule: a container per GPU type). Pick it with
# the VARIANT build arg:
#     VARIANT=cpu     CPU-only torch      <- used for ALL testing (no GPU host yet)
#     VARIANT=nvidia  CUDA 12.4 wheels    (needs the nvidia container runtime)
#     VARIANT=rocm    ROCm 6.2 wheels     (needs /dev/kfd + /dev/dri)
# Nothing in this file is specific to one machine: the same image runs on any host of
# the matching kind, which is what makes the per-GPU containers portable.
#
# Models are NOT baked in (upstream's own image pre-downloads ~10 GB of them). They are
# fetched on first use into a mounted volume, so the image stays a few GB and one model
# cache can be shared/backed up. See docker-compose.yml.
# ============================================================================
FROM python:3.11-slim

ARG VARIANT=cpu
ARG UPSTREAM_REPO=https://github.com/zyddnys/manga-image-translator
ARG UPSTREAM_SHA=95227a2bb0fd306cd4f0c104d57284026f991b3a

# OpenCV/Pillow runtime libs + a compiler (a couple of their deps build from source) +
# git to fetch the pinned upstream revision + ffmpeg for their video/multi-image paths.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git g++ libgl1 libglib2.0-0 libsm6 libxext6 libgomp1 ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN git clone "$UPSTREAM_REPO" /app/upstream \
    && cd /app/upstream && git checkout "$UPSTREAM_SHA" \
    && git rev-parse HEAD > /app/UPSTREAM_SHA.txt \
    && git log -1 --format='%H %ad %s' --date=short >> /app/UPSTREAM_SHA.txt

# Torch FIRST and from the index that matches the accelerator, so requirements.txt (which
# pins nothing) is satisfied by it instead of pulling the default CUDA build everywhere.
RUN set -eux; \
    case "$VARIANT" in \
      cpu)    pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu ;; \
      nvidia) pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cu124 ;; \
      rocm)   pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/rocm6.2 ;; \
      *) echo "unknown VARIANT=$VARIANT (cpu|nvidia|rocm)"; exit 1 ;; \
    esac

RUN pip install --no-cache-dir -r /app/upstream/requirements.txt

ENV PYTHONPATH=/app/upstream \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/upstream/models/.hf

WORKDIR /app/upstream
EXPOSE 8000

# Their FastAPI service. --use-gpu is added by the compose file for the GPU variants only.
CMD ["python", "server/main.py", "--host", "0.0.0.0", "--port", "8000"]
