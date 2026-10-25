# Portable AI — offline analyst workstation

A self-contained local AI toolkit for Windows that runs from a USB drive with **no install,
no admin rights, and no internet** after the initial download. It combines a local chat model,
static analysis of Windows binaries, and search over your own documents.

```
Start-AI.cmd   ->   http://127.0.0.1:8099   ->  Chat / Binary Lab / Library / Settings
```

---

## Quick start

1. Copy this whole folder to the USB drive (or run `tools\deploy-to-usb.ps1`).
2. Double-click **`Start-AI.cmd`**.
3. Your browser opens the UI. The first answer takes a few seconds while the model loads.

To stop: close the console window, or run **`Stop-AI.cmd`**.

Nothing is installed on the host PC. All state lives in `data\` next to the app.

### Cloning this repository

GitHub does not store the two model files or the portable runtime here (the chat model alone is
2.38 GB, above GitHub's file limit). The app, docs and build scripts are in Git; rebuild the
payload once with an internet connection:

```powershell
powershell -ExecutionPolicy Bypass -File tools\fetch-runtime.ps1
powershell -ExecutionPolicy Bypass -File tools\fetch-model.ps1
```

Then run `Start-AI.cmd`. Both scripts download pinned versions and verify size and SHA-256; no
Python, Node.js or admin rights are needed for this step. The result is the same tree as the
prebuilt USB copy.

---

## What is inside

| Piece | Choice | Size |
|---|---|---|
| Chat model | Qwen3-4B-Instruct-2507, Q4_K_M GGUF | 2.33 GB |
| Embedding model | nomic-embed-text-v1.5, Q4_K_M | 80 MB |
| Inference engine | llama.cpp b11871 (Vulkan + CPU builds) | 60 MB |
| Runtime | Python 3.13.5 embeddable + pefile, pypdf, capstone | 192 MB |
| App | stdlib Python HTTP server + vanilla JS/CSS (no build step) | < 1 MB |
| **Total** | | **~2.7 GB** |

No Node.js, no browser extension, no external fonts, no CDN, no telemetry.

### Features

**Chat** — streaming answers, stop button, Markdown with copyable code blocks. Chat history is
stored in your browser's `localStorage`, not on the server.

**Binary Lab** — drop an `.exe`, `.dll`, `.sys`, `.scr`, `.ocx`, `.cpl` or `.bin` (up to 512 MB),
or type a path. For Windows PE files it produces a dossier with:

- hashes (MD5/SHA-1/SHA-256), file kind, architecture, .NET detection
- DOS/NT headers, section table with entropy and flags, overlay
- imports by DLL, exports, imphash, delay-load imports
- resources (including embedded-PE detection), version info, manifest
- Authenticode presence, debug directory / PDB path
- classified strings (URLs, IPs, emails, registry keys, paths, domains, keywords)
- entry-point disassembly (40 instructions via capstone)
- capability *indicators* — never presented as a verdict

Every report can be explained by the local model, and the dossier copied as text. Reports are
saved under `data\reports\` and can be opened with `/?report=<id>`.

**Library** — index PDF, DOCX/PPTX/XLSX, TXT, MD, RST, LOG, JSON, CSV/TSV, HTML and source files,
individually or by folder. Search is hybrid: SQLite FTS5 BM25 plus nomic embeddings, fused with
reciprocal rank fusion. Answers that use the library cite `[S1]`, `[S2]` … with file names and
page numbers. If the embedding backend is unavailable, search falls back to keyword-only.

**Settings** — pick a model, context size, GPU layers, backend (auto/Vulkan/CPU), mode
(auto/external/bundled) and temperature; restart or stop the model; toggle LAN access; view GPU,
RAM and storage.

**LAN (optional)** — `Start-AI-LAN.cmd` binds to all interfaces and requires a random token.
The token URL is shown in Settings and on the console. Intended for a phone/tablet on the same
home network; do not expose it to the internet.

---

## Measured performance

Machine used for all measurements: **Ryzen 7 8845HS, 16 GB RAM, RTX 4050 Laptop 6 GB,
Windows 11 build 26300, driver 32.0.16.2101**.

| Backend | Model load | Prefill (~4.4k tokens) | Generation |
|---|---|---|---|
| **Vulkan (shipped)** | 2.6 s | 2,443 tok/s | 50.1 tok/s |
| CPU (shipped fallback) | 3.6 s | 99 tok/s | 9.7 tok/s |
| CUDA 12.4 (evaluated, not shipped) | 4.1 s | 2,750 tok/s | 48.7 tok/s |
| Vulkan, cold start from a USB 2.0 stick | 76.3 s | — | 54.6 tok/s |

CUDA was only ~12% faster on prefill, no faster on generation, and would add ~1.2 GB plus an
NVIDIA-only requirement, so the build ships Vulkan with automatic CPU fallback.
Through the app, a 3,969-token report prompt prefilled in 1.83 s and answered at ~50 tok/s.
VRAM use is ~4.0–4.1 GB of 6.1 GB with an 8k context.

The USB row is a real cold start: the model copy was written with unbuffered I/O so Windows could
not serve it from cache, and raw reads measured 35 MB/s. On that SanDisk Cruzer Blade (USB 2.0),
expect about **1–1.5 minutes before the first answer** after plugging the drive in; once the model
is in the OS cache, restarts are fast. A USB 3.x drive or copying the folder to the internal disk
removes most of that wait. Startup timeout is 300 s per backend to accommodate slow sticks.

---

## Requirements

- 64-bit Windows 10/11 (tested on Windows 11 x64).
- Any x64 CPU. A Vulkan-capable GPU (NVIDIA/AMD/Intel) is strongly recommended; the bundled CPU
  fallback works but is ~5× slower.
- ~3 GB free disk/USB space (the drive must be writable for `data\` and `logs\`).
- exFAT or NTFS for the USB drive (FAT32 is not recommended; single files can exceed 4 GB in
  future model updates).
- No admin rights, no Python install, no Visual C++ redistributable install required (the engine's
  runtime DLLs are bundled app-local). LAN mode may need a firewall approval from an administrator.

If a local OpenAI-compatible server is already running (for example LM Studio on port 1234),
mode `auto` uses it instead of starting the bundled engine. See `docs\SETUP.md`.

---

## Verified vs. not verified

Tested on the build machine against the live app:

- bundled Vulkan startup (~3 s) and CPU fallback; streaming chat; stop/restart
- PE analysis of `notepad.exe` (8 sections, 315 imports, 50 DLLs, 7 resources, 1,529 strings)
- a padded 60 MB PE upload (analyzed in 3.5 s) and a >512 MB rejection
- PDF/DOCX/MD indexing and hybrid search with `[S1]` citations
- report explanation with dossier grounding (no invented citations)
- desktop/tablet/phone layout: no horizontal overflow, mobile tab bar pinned above the input,
  contrast ≥ 5.1:1 for text
- LAN mode: token gate, cookie, CSRF header, chat — from the same machine via its LAN IP
- loopback-only binding in normal mode, DNS-rebinding Host check, exit codes
- deployment to a USB 2.0 stick (SanDisk Cruzer Blade, exFAT): destination self-check passed,
  app launched from the stick, real cold model load 76 s, chat answered at 54.6 tok/s
- LAN mode started from the USB copy: login gate and token-gated status request

Not verified by the author:

- a real phone/tablet on the LAN (tested via the laptop's own LAN IP only)
- Intel/AMD GPUs, other Windows builds, other drivers
- behavior on a FAT32 drive or a read-only network share
- screenshots were generated headlessly and the layout was checked programmatically rather than
  by human eyeballing. Files are in `docs\screenshots\` for you to review.

### Screenshots

| File | What it shows |
|---|---|
| `docs\screenshots\chat-empty.png` | empty chat, desktop |
| `docs\screenshots\chat-answer.png` | streaming answer with stats |
| `docs\screenshots\binary-lab.png` | PE report overview and observations |
| `docs\screenshots\library.png` | hybrid document search results |
| `docs\screenshots\mobile-chat.png` | phone layout with bottom tab bar |

The documents, reports and questions in the screenshots are test data used during verification;
they are not included in the shipped tree.

The tool is a static-analysis assistant, not an antivirus. It never executes uploaded files and
does not prove that a binary is safe or malicious.

---

## Folder layout

```
PortableAI\
  Start-AI.cmd            normal start (loopback only)
  Start-AI-LAN.cmd        start and listen on the LAN (token required)
  Stop-AI.cmd             stop server and model processes
  START-HERE.txt          one-page quick start
  app\
    server.py             HTTP server, routes, SSE, auth
    core\                 config, paths, LLM runtime, PE analysis, RAG, extraction
    web\                  index.html, style.css, app.js, favicon.svg
  models\                 main GGUF + embeddings\
  runtime\
    python\               embeddable Python 3.13
    site-packages\        pefile, pypdf, capstone
    llama\vulkan|cpu\     llama-server and DLLs
  config\config.json      generated on first run (includes a LAN token)
  data\                   reports, library DB, uploads, server.pid
  logs\                   llama-server.log, llama-embed.log
  docs\                   setup, portability, troubleshooting, safety, screenshots
  tools\                  fetch-model, fetch-runtime, verify-install, deploy-to-usb
```

All paths are resolved relative to the project folder, so the drive letter does not matter.

---

## Documentation

- [docs/SETUP.md](docs/SETUP.md) — first run, settings, external model server, LAN
- [docs/PORTABILITY.md](docs/PORTABILITY.md) — USB deployment, other PCs, moving the folder
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — symptom-by-symptom fixes
- [docs/SECURITY-AND-SEB.md](docs/SECURITY-AND-SEB.md) — security model, privacy, and the
  legitimate scope of Safe Exam Browser testing

## Safety and authorized use

Use the Binary Lab only on files you own or are authorized to inspect. For behavior questions,
run samples in an isolated VM — this app deliberately cannot execute them.

This project will not help bypass exam proctoring, DRM, or access controls. The Safe Exam Browser
document explains what is in scope (authorized lab verification, accessibility troubleshooting,
static inspection of a copy you may test) and what is not.

## Credits and licenses

- [llama.cpp](https://github.com/ggml-org/llama.cpp) — MIT; license files are kept in
  `runtime\llama\`.
- Qwen3-4B-Instruct-2507 — Apache-2.0 (Alibaba Cloud).
- nomic-embed-text-v1.5 — Apache-2.0 (Nomic AI).
- Python 3.13 — PSF license (`runtime\python\LICENSE.txt`).
- pefile (MIT), pypdf (BSD-3-Clause), capstone (BSD-3-Clause).

The prebuilt USB build redistributes the model and runtime binaries for offline use; keep their
license files with the drive. They are not stored in this Git repository — use the fetch scripts
above to rebuild them.
