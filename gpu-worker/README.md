# Manga Fill GPU worker

A standalone FastAPI service that runs the three vision models on a GPU (or CPU)
and exposes them over HTTP for the Manga Fill app:

| Endpoint           | Model                 | Purpose                       |
|--------------------|-----------------------|-------------------------------|
| `POST /detect-ocr` | RT-DETR-v2 + manga-ocr| detect bubbles + OCR Japanese |
| `POST /inpaint`    | LaMa (big-lama)       | erase original Japanese text  |
| `GET /health`      | —                     | `device`, `backend`, `version`|

The app calls these only when **Settings → GPU → worker URL** is set; when the
worker is down it silently falls back to its own CPU models, so a dead GPU never
breaks a job. Translation stays cloud-side in the app.

> **First time setting this up?** Follow the step-by-step walkthrough in
> **[SETUP_GUIDE.md](SETUP_GUIDE.md)** — it covers every GPU type end to end.

## Pick your image

Four images, identical code and API — only the base image / torch build differs:

| Dockerfile             | Image tag                 | Runs on                       | Base image                                              |
|------------------------|---------------------------|-------------------------------|---------------------------------------------------------|
| `Dockerfile`           | `mangafill-gpu:latest`    | NVIDIA RTX 20 / 30 / 40 / 50  | `pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime`         |
| `Dockerfile.pascal`    | `mangafill-gpu:pascal`    | NVIDIA P2000 / GTX 10 (Pascal)| `pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime`         |
| `Dockerfile.rocm`      | `mangafill-gpu:rocm`      | AMD RX 6000 / 7000 / 9000     | `rocm/pytorch:rocm7.0.2_ubuntu22.04_py3.11_pytorch_release_2.9.1` |
| `Dockerfile.cpu`       | `mangafill-gpu:cpu`       | No GPU (test / CPU offload)   | `python:3.11-slim` + torch CPU wheel                    |

### GPU support matrix

- **NVIDIA RTX 20/30/40/50** — one image (`Dockerfile`, torch 2.7.1 + CUDA 12.8),
  covering Turing (sm_75) through Blackwell (sm_120). This is what almost every
  modern card uses.
- **NVIDIA Pascal (P2000 / GTX 10-series)** — separate image (`Dockerfile.pascal`,
  torch 2.5.1 + CUDA 12.4), because PyTorch 2.6+ dropped Pascal (sm_61). This is
  the *only* reason the split exists.
- **AMD RX 6000/7000/9000** — one image (`Dockerfile.rocm`, ROCm 7.0.2 + torch 2.9.1),
  covering RDNA2 (gfx1030), RDNA3 (gfx1100/1101/1102), RDNA4 (gfx1200/1201).
  **RDNA1 (RX 5000) is NOT supported** by ROCm.
- **CPU** — anything, just slower.

## Quick start (compose)

```bash
cd gpu-worker

# 1. Build the image for YOUR gpu:
docker build -t mangafill-gpu:latest  .                       # NVIDIA RTX 20/30/40/50
docker build -t mangafill-gpu:pascal -f Dockerfile.pascal .   # NVIDIA P2000 / GTX 10
docker build -t mangafill-gpu:rocm   -f Dockerfile.rocm  .    # AMD RX 6000/7000/9000
docker build -t mangafill-gpu:cpu    -f Dockerfile.cpu   .    # no GPU

# 2. Start it (profile = nvidia | pascal | rocm | cpu):
docker compose --profile nvidia up -d
```

## Verify

```bash
curl http://<host>:9001/health
# NVIDIA: {"status":"ok","device":"cuda","backend":"cuda","version":"0.2.0"}
# AMD:    {"status":"ok","device":"cuda","backend":"rocm","version":"0.2.0"}
# CPU:    {"status":"ok","device":"cpu","backend":"cpu","version":"0.2.0"}

curl -F "image=@page.jpg" http://<host>:9001/detect-ocr
# -> {"bubble":[[...]],"blocks":[{"bbox":[...],"text":"こんにちは","orientation":"vertical"}]}
```

## Point Manga Fill at it

Dashboard → **Settings → GPU** → enter `http://<host>:9001` in **GPU worker URL**
→ **Save**. The status badge flips to **External GPU (connected)**.

## Licensing

All three models are Apache-2.0 (RT-DETR-v2 via `ogkalu`, manga-ocr via
`kha-white`, LaMa via `simple_lama_inpainting`/`advimman`). Weights are pulled at
build time, not redistributed.
