# CPU-first image (Phase 1). GPU acceleration is wired later as a remote
# worker on the GPU host — never in this container.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Native deps for OpenCV/Pillow (grows when ML milestones land).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .

EXPOSE 8788

# Single worker — all state is in SQLite, not process memory (Subber lesson).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8788", "--workers", "1"]
