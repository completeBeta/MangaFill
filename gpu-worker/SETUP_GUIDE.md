# GPU Worker — Complete Setup Guide

This walks you through installing the Manga Fill GPU worker from scratch, for
every GPU type. Follow the section that matches your hardware.

**What the worker is:** a small background service that runs the three vision
models (bubble/text detection, OCR, and text-erasing) on your GPU and serves them
over HTTP. The Manga Fill app calls it when you point it at the worker URL. If
the worker is ever down, the app silently falls back to CPU — so this is a
"make it faster" add-on, never a hard requirement.

> **Pre-built images — no build needed.** Pull the image for your GPU from
> `ghcr.io/completebeta/manga-fill-gpu:<tag>` (tags: `latest`, `pascal`, `rocm`,
> `cpu`). The `docker build` commands below are only for developers building from
> source.

---

## Step 0 — Which image do I need?

| Your GPU                          | Dockerfile           | Image tag               | Profile    |
|-----------------------------------|----------------------|-------------------------|------------|
| NVIDIA RTX 20 / 30 / 40 / 50      | `Dockerfile`         | `mangafill-gpu:latest`  | `nvidia`   |
| NVIDIA P2000 / GTX 10 (Pascal)    | `Dockerfile.pascal`  | `mangafill-gpu:pascal`  | `pascal`   |
| AMD RX 6000 / 7000 / 9000         | `Dockerfile.rocm`    | `mangafill-gpu:rocm`    | `rocm`     |
| No GPU                            | `Dockerfile.cpu`     | `mangafill-gpu:cpu`     | `cpu`      |

**How to identify your card:**

- **NVIDIA** — run `nvidia-smi`. The first line names it: "RTX 3060" / "RTX 4070"
  / "RTX 5090" → use `Dockerfile`. "P2000" / "GTX 1080" / "GTX 1060" → use
  `Dockerfile.pascal`.
- **AMD** — run `lspci | grep -i vga`. "RX 6800" / "RX 7900" / "RX 9070" → use
  `Dockerfile.rocm`. "RX 5700" / "RX 5500" → **not supported** (no ROCm) — use
  `Dockerfile.cpu`.
- **No dedicated GPU** (integrated only, or a server) → `Dockerfile.cpu`.

---

## Step 1 — Prerequisites (do once per machine)

1. **Docker** installed and working:
   ```bash
   docker --version          # prints a version number
   ```
2. **Docker Compose** (recommended):
   ```bash
   docker compose version    # prints a version number
   ```
3. **Get the worker code.** From the MangaFill repo, you only need the
   `gpu-worker/` folder:
   ```bash
   git clone https://github.com/completeBeta/MangaFill.git
   cd MangaFill/gpu-worker
   ```
   (No git? Download the repo ZIP from GitHub and unzip it, then `cd` into
   `gpu-worker/`.)
4. **Make the GPU visible to Docker** — pick your vendor:

   - **NVIDIA on plain Linux:** install the NVIDIA Container Toolkit, then restart Docker:
     ```bash
     sudo apt-get install -y nvidia-container-toolkit   # or your distro's equivalent
     sudo systemctl restart docker
     ```
   - **NVIDIA on Unraid:** install the "Nvidia Driver" plugin — see Step 2.
   - **AMD on Linux:** install ROCm (see AMD section, Step 4) and confirm
     `/dev/kfd` and `/dev/dri` exist.

---

## Step 2 — NVIDIA (RTX 20 / 30 / 40 / 50) — the common case

### 2a. On Unraid

1. Install the driver plugin:
   - **Apps** → search **"Nvidia Driver"** (author `ich777`) → **Install**.
2. Compile/download the driver:
   - **Settings → Nvidia Driver → Download driver**. It compiles against your
     kernel version; wait until it reports done.
3. Note your GPU's **UUID** on that same page (looks like
   `GPU-9cfdd18c-2b41-b158-f67b-720279bc77fd`).
4. Build the image. Open a terminal on the box and run, from inside `gpu-worker/`:
   ```bash
   docker build -t mangafill-gpu:latest .
   ```
   (First build downloads ~2 GB of base image + model weights; a few minutes.)
5. Create the container:
   - **Docker → Add Container**.
   - **Repository:** `mangafill-gpu:latest`
   - Toggle **Advanced view** (top-right) and set:
     - **Extra Parameters:** `--runtime=nvidia`
     - **Variable** `NVIDIA_VISIBLE_DEVICES` → your GPU **UUID** (from step 3)
     - **Variable** `NVIDIA_DRIVER_CAPABILITIES` → `all`
   - **Port:** add a mapping — host `9001` → container `9001`.
   - **Apply.**

### 2b. On plain Linux (compose)

```bash
cd gpu-worker
docker build -t mangafill-gpu:latest .
docker compose --profile nvidia up -d
```

---

## Step 3 — NVIDIA Pascal (P2000 / GTX 10-series)

Same worker, but built from `Dockerfile.pascal` — Pascal (sm_61) was dropped from
PyTorch 2.6+, so this image pins the last compatible torch (2.5.1).

### On Unraid (P2000 — the usual case)

1. Install the driver plugin:
   - **Apps** → search **"Nvidia Driver"** (author `ich777`) → **Install**.
2. Compile/download the driver:
   - **Settings → Nvidia Driver → Download driver**. Wait for it to finish
     (it compiles against your kernel — a few minutes).
3. Note your P2000's **UUID** on that page (looks like `GPU-9cfdd18c-...`).
4. Get the `gpu-worker/` folder onto Unraid — git clone the repo, download the
   ZIP from GitHub, or copy the folder over SMB.
5. Build the image — open a terminal on Unraid and run, from inside `gpu-worker/`:
   ```bash
   docker build -t mangafill-gpu:pascal -f Dockerfile.pascal .
   ```
   (First build downloads the ~3 GB base image + model weights; a few minutes.)
6. Create the container:
   - **Docker → Add Container**.
   - **Repository:** `mangafill-gpu:pascal`
   - Toggle **Advanced view** (top-right) and set:
     - **Extra Parameters:** `--runtime=nvidia`
     - **Variable** `NVIDIA_VISIBLE_DEVICES` → your P2000 **UUID** (from step 3)
     - **Variable** `NVIDIA_DRIVER_CAPABILITIES` → `all`
   - **Port:** host `9001` → container `9001`.
   - **Apply.**

### On plain Linux

```bash
cd gpu-worker
docker build -t mangafill-gpu:pascal -f Dockerfile.pascal .
docker compose --profile pascal up -d
```

> **Why a separate image?** PyTorch 2.6+ dropped Pascal (sm_61), so this image
> pins torch 2.5.1 + CUDA 12.4 — the last line with Pascal kernels. Every newer
> NVIDIA card uses the default image (2.7.1).

---

## Step 4 — AMD (RX 6000 / 7000 / 9000)

1. **Install ROCm** on the host (Ubuntu 22.04/24.04 are supported):
   - Follow https://rocm.docs.amd.com — install `amdgpu-dkms` and the `rocm`
     runtime for your OS.
2. **Confirm the GPU devices exist:**
   ```bash
   ls -l /dev/kfd /dev/dri
   # both should exist; /dev/dri should list renderD* and card*
   ```
3. **Build + run:**
   ```bash
   cd gpu-worker
   docker build -t mangafill-gpu:rocm -f Dockerfile.rocm .
   docker compose --profile rocm up -d
   ```
   Or run the container directly:
   ```bash
   docker run -d --name mangafill-gpu \
     --device /dev/kfd --device /dev/dri \
     --group-add video --group-add render \
     --security-opt seccomp=unconfined \
     -p 9001:9001 mangafill-gpu:rocm
   ```

> **AMD on Unraid is less turnkey than NVIDIA.** You must pass `/dev/kfd` and
> `/dev/dri` through in the Docker UI and confirm the `amdgpu` driver is loaded.
> If you hit a wall, the simplest path is to run the AMD worker on a plain Linux
> machine (any box with the card in it) and point Manga Fill at it over the
> network — that's exactly what the remote-worker design is for.

---

## Step 5 — CPU (no GPU)

```bash
cd gpu-worker
docker build -t mangafill-gpu:cpu -f Dockerfile.cpu .
docker compose --profile cpu up -d
```

Use this to test the worker end-to-end on a machine with no GPU, or to offload
CPU inference to a separate box so the app host stays light.

---

## Step 6 — Point Manga Fill at the worker

1. Open the Manga Fill dashboard → **Settings → GPU**.
2. In **GPU worker URL**, enter `http://<worker-host>:9001` — use the IP or
   hostname of the machine running the worker (e.g. `http://gpu-host:9001`).
3. Click **Save GPU settings**.
4. The status badge should flip to **External GPU (connected)**.

> The app keeps working even if the worker is unreachable — it just falls back
> to CPU. So if the badge says "External GPU (down)", jobs still run, just slower.

---

## Step 7 — Verify it's working

```bash
curl http://<worker-host>:9001/health
```

Expected output:

| Your setup | `device` | `backend` |
|---|---|---|
| NVIDIA (any) | `cuda` | `cuda` |
| AMD | `cuda` | `rocm` |
| CPU | `cpu` | `cpu` |

Then run a real page through it (any manga page image):

```bash
curl -F "image=@page.jpg" http://<worker-host>:9001/detect-ocr
```

A working worker returns JSON with a `bubble` list and a `blocks` list containing
OCR'd Japanese text. If `/health` says `"device":"cpu"` on a GPU box, the GPU
didn't attach — go to troubleshooting.

---

## Step 8 — Troubleshooting

| Symptom                                    | Fix                                                                                      |
|--------------------------------------------|------------------------------------------------------------------------------------------|
| `/health` → `"device":"cpu"` on a GPU box  | GPU not attached. NVIDIA: re-check `--runtime=nvidia` + the UUID. AMD: confirm `/dev/kfd` + `/dev/dri` passed. |
| `nvidia-container-cli: ... driver not loaded` | Nvidia Driver plugin not fully installed — re-download the driver and restart Docker.   |
| AMD RX 5000 shows no device                | RDNA1 has no ROCm support — use the CPU image.                                           |
| Build fails pulling a base image           | Retry (transient network). All four base-image tags are published and verified.          |
| Out of memory on a small card              | The worker processes one request at a time already; report the page if it still OOMs.    |
| Badge says "External GPU (down)"           | Worker not reachable from the app — check the host/port and that the container is running. |

---

## Which models run where (sanity reference)

- **On the worker (GPU or CPU):** RT-DETR-v2 detection, manga-ocr, LaMa inpainting.
- **On the app, in the cloud (never the GPU):** the LLM translation step.

So the worker only ever accelerates the *vision* half of the pipeline; translation
cost/behaviour is unaffected by which worker image you run.
