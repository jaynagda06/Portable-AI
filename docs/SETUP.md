# Setup

This build is already complete: models, runtime and the app are in the folder. "Setup" means
deciding where it lives and how it should start. No installer runs and nothing is written outside
this folder.

---

## 1. First run

1. Put the folder somewhere writable — USB drive, external SSD, or a normal folder.
   Avoid `C:\Program Files` (needs admin) and read-only shares.
2. Double-click `Start-AI.cmd`.
3. The console shows the app URL and the model name; your default browser opens
   `http://127.0.0.1:8099`.
4. Send a short message. The first request waits for the model to load (~3 s on the test laptop,
   longer on CPU-only PCs).

The status pill in the sidebar shows `ready` when the model is available. If it stays on
`starting`, see `docs\TROUBLESHOOTING.md`.

To stop, run `Stop-AI.cmd` or close the console window. Closing the browser does **not** stop the
model; use `Stop-AI.cmd` to free VRAM.

---

## 2. Choose the runtime mode

Open **Settings → Model runtime** and **Model settings**.

| Setting | Values | Meaning |
|---|---|---|
| Mode | `auto` (default) | Use a local OpenAI-compatible server if one answers, otherwise start bundled llama.cpp |
| | `bundled` | Always start the bundled llama.cpp server |
| | `external` | Only talk to `external_url`; fail if it is not running |
| Backend | `auto` | Try Vulkan, fall back to CPU |
| | `vulkan` | Force the Vulkan build |
| | `cpu` | Force the CPU build (slow, useful on VMs without GPU passthrough) |
| Context | 1024–65536 | Tokens of prompt + answer kept in memory. 8192 is the tested default |
| GPU layers | 0–999 | `999` offloads everything. Lower it if VRAM is tight |
| Temperature | 0–1.5 | 0.3 default; lower for factual answers |

Changes are saved to `config\config.json` and the model restarts. Use **Restart model** after
editing the file by hand.

### Using LM Studio or another local server

1. Start LM Studio's local server (OpenAI-compatible) on `http://127.0.0.1:1234`.
2. Set **Mode** to `auto` (or `external`) and save.
3. The app probes `/v1/models` and uses the first available model.

The external server keeps its own model and context settings. The bundled 4B model is not used in
that case, which is useful if you already have a larger model and enough VRAM.

### Using a different GGUF with the bundled engine

Put the file in `models\`, reload **Settings**, and pick it under **Model file**. The engine runs
whatever file is selected, but the default context and offload settings are tuned for the 4B
Q4_K_M model.

---

## 3. Add documents to the library

**Library → Add documents**:

- **Drop files** — PDF, DOCX/PPTX/XLSX, TXT, MD, RST, LOG, JSON/JSONL, CSV/TSV, HTML, and source
  files (`.py`, `.js`, `.ts`, `.cs`, `.c`, `.cpp`, `.ps1`). Upload limit: 100 MB per file.
- **Index folder** — type a path such as `D:\Notes` or `%USERPROFILE%\Documents\Lab`.

Indexing extracts text, chunks it (~1,400 chars with 200-char overlap), stores it in
`data\index\library.db`, and computes embeddings with the bundled nomic model. The embedding
server starts lazily on the first index/search and uses a random loopback port.

Buttons: **Re-embed all** (after changing embedding settings), **Refresh**, and per-document
delete in the table.

---

## 4. LAN access (phone/tablet)

1. Open **Settings → LAN access** and click **Enable / disable** so the chip reads `on`.
2. Restart the app with **`Start-AI-LAN.cmd`** (or close the console and run `Start-AI.cmd`; the
   saved setting is read at startup).
3. The console and Settings show a URL like
   `http://192.168.0.101:8099/?t=<token>`. Open it on the phone.
4. The token is also stored in `config\config.json` under `server.lan_token`.

Notes:

- The token is required for every page and API call. The first page load sets an HttpOnly cookie;
  the token then disappears from normal navigation.
- If Windows Firewall prompts, allow **private networks** only.
- Everyone on that LAN who has the URL can use the app while it runs. Disable LAN access when
  finished, and never port-forward it to the internet.
- The UI is responsive down to phone width: bottom tab bar, stacked cards, scrollable tables.

---

## 5. Binary analysis workflow

1. **Binary Lab** → drop a file or enter a path → **Analyze path**.
2. Review the tabs: Overview, Sections, Imports, Exports, Resources, Strings, Disassembly.
3. Click **Explain with AI** to have the local model interpret the dossier. It is instructed to
   tie claims to fields and to avoid verdicts.
4. Use **Copy dossier** to paste the evidence into a ticket, or copy the SHA-256 from the header.

Uploads are streamed to `data\uploads\`, analyzed, and deleted immediately. Reports stay in
`data\reports\` until you delete them. Re-analyzing the same file is fast because the report is
keyed by hash.

Limits: 512 MB per binary, 128 MB of content scanned for strings, 4,000 strings displayed,
40 disassembly instructions.

---

## 6. Repairs and rebuilds

The shipped tree is already built. If a file is lost or you want to rebuild on another machine:

```powershell
# check what is missing / broken
powershell -ExecutionPolicy Bypass -File tools\verify-install.ps1

# re-download the two GGUF models (~2.4 GB)
powershell -ExecutionPolicy Bypass -File tools\fetch-model.ps1

# rebuild Python runtime, packages and llama.cpp (needs internet once)
powershell -ExecutionPolicy Bypass -File tools\fetch-runtime.ps1
```

`fetch-runtime.ps1` pins Python 3.13.5, llama.cpp b11871, pefile 2024.8.26, pypdf 6.21.2 and
capstone 5.0.9 — the versions this build was tested with.

---

## 7. Files created at runtime

| Path | Contents |
|---|---|
| `config\config.json` | settings + generated LAN token (created on first run) |
| `data\server.pid` | PID used by `Stop-AI.cmd` |
| `data\reports\` | one JSON report per analyzed hash + `_index.json` |
| `data\index\library.db` | documents, chunks, FTS index, embeddings |
| `data\uploads\` | temporary upload staging (files deleted after processing) |
| `logs\llama-server.log` | engine log for the chat model |
| `logs\llama-embed.log` | engine log for the embedding model |

Deleting `data\` resets reports and the library. Deleting `config\config.json` regenerates defaults
and a new LAN token on the next start.
