# Portability

## What "portable" means in this build

- No installer, service, registry key, scheduled task or host-PC Python install.
- The app resolves every path relative to the folder that contains `Start-AI.cmd`
  (`app\core\paths.py`), so the drive letter and folder name do not matter.
- Settings, reports, the document library, and logs live inside the folder
  (`config\`, `data\`, `logs\`).
- Only localhost is contacted. There are no CDNs, fonts, analytics or update checks.
- The browser is the only host dependency; Edge/Chrome/Firefox on Windows all work.

The host PC does need a writable drive and, for GPU speed, a working Vulkan driver. The CPU
fallback runs anywhere without one.

---

## Copying to a USB drive

Target: **~2.7 GB**, plus room for your documents and reports. A 32 GB drive leaves plenty.

1. Stop the app (`Stop-AI.cmd`). Do not copy a running tree — SQLite may have a live WAL file.
2. Either drag the whole folder in Explorer, or use the deploy script:

   ```powershell
   powershell -ExecutionPolicy Bypass -File tools\deploy-to-usb.ps1 -Destination E:\PortableAI
   ```

   The script checks free space, copies the app but not development/test artifacts, and verifies
   the result. To put it at the root of the drive, use `-Destination E:\`.
3. Eject safely, then on the target PC open the folder and run `Start-AI.cmd`.

### Filesystem

- **exFAT** (recommended for cross-PC USB) or **NTFS**.
- Avoid **FAT32**: it caps a single file at 4 GB, which will block future model upgrades. The
  current 2.33 GB model fits, but you would have to delete it before adding a larger one.
- The drive must be writable. A read-only USB or ISO cannot store `data\` and `logs\`.

### First start from USB is slower

The 2.6 s model load measured on the build machine benefits from the Windows file cache. The real
cold-start cost is the time to read the 2.38 GB model from the stick:

- Measured on the build machine with a SanDisk Cruzer Blade **USB 2.0**: 35 MB/s raw read,
  **76 s to first answer**, then 54.6 tok/s generation (GPU speed is unaffected once loaded).
- A **USB 3.x** drive should be roughly 5–10× faster at reading, so expect a few seconds to
  low tens of seconds.
- Subsequent starts in the same Windows session are fast, because the model is cached in RAM.
  Plugging the drive into another PC starts cold again.

Startup timeout is 300 s per backend, so even slow sticks have headroom. If you use the drive often,
copying the folder to the internal disk removes the wait entirely; the app does not care where it
lives.

---

## Moving to another PC

| Host | What to expect |
|---|---|
| NVIDIA GPU with current driver | Vulkan backend, close to the measured numbers |
| AMD/Intel GPU with current driver | Vulkan backend; speed varies by GPU and memory bandwidth |
| VM without GPU passthrough | Automatic CPU fallback (~10 tok/s on the test Ryzen 7) |
| PC without Vulkan support | CPU fallback; still fully functional |
| 8 GB RAM | Works, but close the browser tabs; the model needs ~3 GB plus OS overhead |
| 4 GB VRAM | Reduce **GPU layers** in Settings (e.g. 20–28) so part of the model stays in RAM |

The app probes Vulkan first and falls back to CPU. You can force either under
**Settings → Backend**. `logs\llama-server.log` records which backend was used and any error.

The three Microsoft VC++ runtime DLLs the engine needs (`msvcp140.dll`, `vcruntime140.dll`,
`vcruntime140_1.dll`) are stored next to `llama-server.exe` in both backend folders. That makes the
drive self-contained even on a Windows install without the Visual C++ redistributable. The Universal
CRT (`api-ms-win-crt-*`) is part of Windows 10/11.

### Portability caveats

- **Document paths are absolute.** The library stores the path you indexed. If you move the drive
  to another PC, existing entries remain searchable (their text lives in the database), but
  **Refresh/re-index** of those entries will not find the original files. For a drive that travels,
  index documents stored on the drive itself, or re-add them after moving.
- **The LAN token and settings travel with the folder.** That is convenient, but if you copy the
  folder to someone else, they inherit the token. Delete `config\config.json` before handing a copy
  to another person so a fresh token is generated.
- **Reports and chat history.** Reports travel in `data\reports\`. Chat history is stored by the
  browser, so it does **not** travel with the drive and differs per browser/profile.
- **Absolute app port.** The UI is always on `127.0.0.1:8099`. Running two copies at once will make
  the second fail with "address in use". Run one at a time, or start the second with
  `Start-AI.cmd --port 8100`.

---

## Updating the build

Keep `data\`, `logs\` and `config\` and replace the program:

1. Stop the app.
2. Replace `app\`, `runtime\`, `docs\`, `tools\`, and the `.cmd` launchers.
3. Replace `models\` only if the model changed.
4. Start again and run `tools\verify-install.ps1`.

Configuration is deep-merged with built-in defaults, so new settings appear automatically and
unknown keys are preserved.

---

## Running from a synced folder (OneDrive/Dropbox)

Possible but not recommended while running: sync clients lock and rewrite files, and the SQLite
WAL plus multi-hundred-MB model files cause churn. If you need it, pause sync while the app is
running, or keep only `data\` on the synced path by copying it after each session.

---

## Antivirus and SmartScreen

`llama-server.exe` and the bundled Python are unsigned upstream binaries. Defender or a
third-party AV may quarantine them or SmartScreen may warn on first run.

- Verify the file hash against the llama.cpp release before trusting it.
- If you trust this build, add an exclusion for the folder rather than disabling protection.
- Do not run a copy from an untrusted source. The models and binaries are the only large files;
  `tools\fetch-model.ps1` and `tools\fetch-runtime.ps1` can rebuild them from official URLs.

---

## Clean removal

Stop the app and delete the folder. Nothing was installed elsewhere. If you enabled LAN access and
Windows Firewall created a rule, remove the `Portable AI` rule in Windows Firewall settings.
