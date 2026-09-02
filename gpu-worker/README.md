# Manga Fill GPU worker

Runs the three vision models on an NVIDIA GPU and exposes them over HTTP for the
Manga Fill app:

| Endpoint       | Model                                    | Purpose                          |
|----------------|------------------------------------------|----------------------------------|
| `POST /detect-ocr` | RT-DETR-v2 + manga-ocr                | detect bubbles + OCR Japanese    |
| `POST /inpaint`    | LaMa (big-lama)                       | erase original Japanese text     |
| `GET /health`      | —                                        | device + version (app probes)   |

The app calls these only when its **Settings → GPU → worker URL** is set; when the
worker is down it silently falls back to its own CPU models, so a dead GPU never
breaks a job. Translation stays cloud-side in the app.

---

## Prerequisites

- **Unraid** with Docker (any recent 6.9–7.x).
- An **NVIDIA GPU** with at least Kepler architecture. The target here is a
  **P2000 (Pascal, 5 GB)**.
- The container pins **PyTorch 2.5.1 + CUDA 12.4** — the last release line that
  still ships Pascal (`sm_61`) kernels. **Do not bump past 2.5.x** on this GPU.

---

## Step 1 — enable the GPU for Docker on Unraid

1. Open the **Community Applications** plugin → search **"Nvidia Driver"**
   (author `ich777`) → **Install**.
2. Go to **Settings → Nvidia Driver** → click **Download driver** (it compiles
   against your kernel version). Wait for it to finish.
3. On that same settings page, note your GPU's **UUID** (looks like
   `GPU-9cfdd18c-2b41-b158-f67b-720279bc77fd`). You'll use it in Step 3.

## Step 2 — get the worker code onto Unraid

Either path works:

- **git clone** (needs git on Unraid, or download the zip):
  ```bash
  git clone https://github.com/completeBeta/Manga-Fill.git
  cd Manga-Fill/gpu-worker
  ```
- Or copy just the `gpu-worker/` folder over (SMB, scp, USB).

## Step 3 — build the image

```bash
cd gpu-worker
docker build -t mangafill-gpu:latest .
```

The build downloads the PyTorch/CUDA base image (~2 GB) and the model weights
(~0.5 GB) once, and bakes the models in, so the first request after start is
instant. Expect a few minutes on a fast connection.

## Step 4 — run the container

**Via the Docker UI** (recommended):

1. Docker tab → **Add Container**.
2. **Repository:** `mangafill-gpu:latest`.
3. Toggle **Advanced view** (top-right) and set:
   - **Extra Parameters:** `--runtime=nvidia`
   - **Variable** `NVIDIA_VISIBLE_DEVICES` → your GPU UUID from Step 1
   - **Variable** `NVIDIA_DRIVER_CAPABILITIES` → `all`
4. **Port:** add a mapping, e.g. host `9001` → container `9001`.
5. **Apply.**

**Via compose** (if you use the Compose plugin):

```bash
cd gpu-worker
docker compose up -d
```

## Step 5 — point Manga Fill at it

In the Manga Fill dashboard → **Settings → GPU**, enter the worker URL
(e.g. `http://gpu-host:9001`) and **Save**. The status probe should flip to
**connected**.

---

## Verify it's working

```bash
# device should report "cuda"
curl http://<unraid-host>:9001/health
# {"status":"ok","device":"cuda","version":"0.1.0"}

# run a real page through it (any manga page image)
curl -F "image=@page.jpg" http://<unraid-host>:9001/detect-ocr
# -> {"bubble":[[...]],"blocks":[{"bbox":[...],"text":"こんにちは","orientation":"vertical"}]}
```

If `/health` reports `"device":"cpu"`, the GPU didn't attach — re-check the
`--runtime=nvidia` Extra Parameter and `NVIDIA_VISIBLE_DEVICES`.

---

## Troubleshooting

- **`device: cpu`** → GPU not attached. Confirm the Nvidia Driver plugin driver
  downloaded successfully, and the container has `--runtime=nvidia` +
  `NVIDIA_VISIBLE_DEVICES`.
- **`nvidia-container-cli: ... driver not loaded`** → driver plugin not fully
  installed; re-download the driver in the plugin settings and restart Docker.
- **Build fails pulling `pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime`** → that
  tag is published on Docker Hub; retry (transient network). Do not substitute a
  2.6+ image — it drops Pascal support.
- **Out of memory on a 5 GB card** → the worker runs models one request at a time
  (single uvicorn worker); if a large page still OOMs, the app's inpaint already
  downscales only oversized crops. Report the page if it persists.

## Licensing

All three models are Apache-2.0 (RT-DETR-v2 via `ogkalu`, manga-ocr via
`kha-white`, LaMa via `simple_lama_inpainting`/`advimman`). Nothing here is
redistributed in source — weights are pulled at build/runtime, same as the app.
