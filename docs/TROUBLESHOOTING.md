# Troubleshooting

Start with the built-in check — it verifies Python, packages, models, engine binaries, write
access, the library DB and the GPU, without starting the model:

```powershell
powershell -ExecutionPolicy Bypass -File tools\verify-install.ps1
```

The app UI also shows live state under **Settings → Model runtime** and in the sidebar status pill.
Engine logs are in `logs\llama-server.log` and `logs\llama-embed.log`.

---

## The browser does not open

- Open `http://127.0.0.1:8099` manually.
- If the console shows a different port (for example after `--port 8100`), use that port.
- Starting with `--no-browser` suppresses the automatic tab; this is intentional.
- A corporate policy can block `http://127.0.0.1`; try Edge or Firefox.

## "Address already in use"

Another copy is running, or something else owns port 8099.

```powershell
netstat -ano | findstr :8099
Stop-AI.cmd
Start-AI.cmd --port 8100
```

`Stop-AI.cmd` kills the server PID and any `llama-server.exe` started from this folder. If a stale
process from a removed copy remains, end that PID in Task Manager.

## Status stays on "starting" or shows "error"

1. Open `logs\llama-server.log`. The last 20 lines usually name the cause.
2. Common causes:
   - **Out of VRAM** (`failed to allocate`, `CUDA error`, `vkAllocateMemory`): close games/browser
     GPU tabs, lower **GPU layers** to 20–28, restart the model.
   - **Context too large**: reduce **Context** from 8192 to 4096.
   - **Corrupt or missing model**: re-run `tools\verify-install.ps1`, then
     `tools\fetch-model.ps1`.
   - **Vulkan driver missing/old**: update the GPU driver, or set **Backend** to `cpu`.
3. Settings → **Restart model** after changing options.
4. Last resort: stop the app, delete `logs\`, start again; if it still fails, set
   **Backend** to `cpu` to confirm the rest of the app works.

## It runs but answers are slow

- Check **Settings → Model runtime**: `backend` should say `vulkan`. If it says `cpu`, the GPU was
  not usable — update the driver or check `nvidia-smi` / GPU vendor tools.
- On a 4 GB GPU, raise **GPU layers** gradually; too many causes VRAM spill and slows everything.
- Reduce **Context** and **max_tokens** for long chats; prefill cost grows with context.
- Close other GPU-heavy apps. A laptop on battery may throttle the GPU.
- A cold USB drive slows the *first* load; it does not slow generation after loading.
- Expected ballpark on the test laptop: ~50 tok/s generation, ~2.4k tok/s prefill (Vulkan); CPU
  fallback is ~10 tok/s generation.

## Model answers stop mid-sentence or the Stop button does nothing

- **Stop** aborts the browser request; the engine may finish the current token before idling.
- If the UI reconnects to a stopped model, press **Restart model** in Settings.
- Very long answers can hit the 1,024-token output cap. Ask for a shorter answer or split the task.

## The sidebar says "ready" but chat fails

- Check the console for `[chat]` errors.
- If an external server was running (LM Studio) and was closed, mode `auto` may still point at it
  briefly. Restart the model, or switch mode to `bundled`.
- Look at `logs\llama-server.log` for a crash after the model loaded.

## Binary analysis fails or looks empty

- The tool analyzes **PE files** in depth. PDFs, ZIPs, ELF and Mach-O get hashes, file kind and
  strings only — that is expected.
- A `.bin` that is not a PE will not have sections/imports.
- Files above 512 MB are rejected (HTTP 413). The first 128 MB are scanned for strings.
- Packed/compressed binaries legitimately show few imports and high entropy. Treat that as an
  indicator, not a verdict; use a VM for behavior.
- If a specific file crashes the parser, the report still gets hashes; check the console traceback
  and keep the sample for a bug report.

## Library search returns nothing useful

- Confirm the document shows `ok` in the indexed table (not `error`).
- Try a distinctive keyword from the document with **Use library** ticked in chat; citations appear
  as `[S1]` under the answer.
- Scanned PDFs with no text layer extract little or no text. OCR is not included; run OCR first.
- If **Settings → Model runtime → Embeddings** is not `ready (semantic search on)`, search falls
  back to keyword-only. Check `logs\llama-embed.log`; the embedding server starts on first use and
  can take a few seconds.
- After changing the embedding model or chunk size, click **Re-embed all**.

## Upload is rejected

| Limit | Value | Where |
|---|---|---|
| Binary upload | 512 MB | `analysis.max_upload_mb` |
| Library file | 100 MB | `rag.max_file_mb` |
| JSON API body | 4 MB | fixed |

Edit `config\config.json` and restart to change the first two. Very large binaries also take
longer to hash.

## LAN page does not load on the phone

- The app must be started in LAN mode: the chip in **Settings → LAN access** reads `on`, and the
  console prints a `LAN : http://192.168.x.x:8099/?t=...` line.
- Use the **exact token URL**, not `localhost` (on a phone, localhost is the phone itself).
- Phone and laptop must be on the same Wi-Fi/LAN and the network must not use client isolation
  (common on guest Wi-Fi).
- Allow the app through Windows Firewall for **private** networks only.
- If the token was copied incorrectly, read it from `config\config.json` or click **Copy URL** in
  Settings.
- A 401 page means the token is missing/wrong; a timeout usually means firewall, subnet mismatch or
  client isolation.

## Windows Defender / SmartScreen blocks a file

The bundled engine is an unsigned upstream binary. Verify the llama.cpp release hash, then add a
folder exclusion if you trust this copy. Do not disable real-time protection globally.

## The app will not stop

`Stop-AI.cmd` reads `data\server.pid` and kills the process tree, then any `llama-server.exe` whose
path is inside this folder. If the console window is gone but VRAM is still used:

```powershell
Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force
```

Then start again. Never kill `llama-server.exe` belonging to another app unless you intend to.

## Reset to a clean state

```powershell
Stop-AI.cmd
Remove-Item data -Recurse -Force      # reports, library, uploads, pid
Remove-Item logs -Recurse -Force      # engine logs
Remove-Item config\config.json        # settings + new LAN token
Start-AI.cmd
```

The folders and a fresh config are recreated automatically. Models and runtime are untouched.

## Reporting a problem

Collect:

- what you clicked and the exact message,
- `Settings → Model runtime` values (mode, backend, endpoint),
- `logs\llama-server.log` (last ~50 lines) and any console traceback,
- output of `tools\verify-install.ps1`,
- GPU model, driver version, Windows build, and whether the folder is on USB or internal disk.

Remove any document text or file paths you do not want to share.
