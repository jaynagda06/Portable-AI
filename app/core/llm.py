"""Local LLM runtime manager.

Supports two interchangeable backends:
  * external  - any OpenAI-compatible server already running (e.g. LM Studio :1234)
  * bundled   - llama.cpp shipped on the stick, started on demand

The rest of the app only talks OpenAI-shaped HTTP, so either works.
"""
from __future__ import annotations

import atexit
import json
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import paths, util

CREATE_NO_WINDOW = 0x08000000


def _http_json(url: str, payload: dict | None = None, timeout: float = 5.0):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


class Runtime:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.proc: subprocess.Popen | None = None
        self.embed_proc: subprocess.Popen | None = None
        self.log_path = paths.LOGS_DIR / "llama-server.log"
        self.embed_log_path = paths.LOGS_DIR / "llama-embed.log"
        self.state = {
            "status": "idle",          # idle | starting | ready | error | stopped
            "mode": None,              # bundled | external
            "endpoint": None,
            "model": None,
            "backend": None,
            "pid": None,
            "error": None,
            "started_at": None,
            "load_seconds": None,
        }
        self.embed_state = {"status": "idle", "error": None, "endpoint": None}
        self._embed_port: int | None = None
        atexit.register(self.stop)

    @staticmethod
    def _free_port() -> int:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])
        finally:
            s.close()

    # ---------------- probes ----------------
    def _models(self, base: str, timeout=1.5):
        try:
            r = _http_json(base.rstrip("/") + "/v1/models", timeout=timeout)
            return [m.get("id") for m in r.get("data", [])]
        except Exception:
            return None

    def _healthy(self, base: str) -> bool:
        try:
            req = urllib.request.Request(base.rstrip("/") + "/health")
            with urllib.request.urlopen(req, timeout=1.2) as r:
                body = r.read().decode("utf-8", "replace")
                if r.status == 200:
                    try:
                        return json.loads(body).get("status") in ("ok", "ready", None)
                    except Exception:
                        return True
        except urllib.error.HTTPError as e:
            if e.code == 503:
                return False  # still loading
        except Exception:
            return False
        return False

    # ---------------- lifecycle ----------------
    def ensure_started(self, wait: bool = True) -> dict:
        with self.lock:
            if self.state["status"] == "ready" and self.state["endpoint"]:
                if self.state["mode"] == "external" or self._healthy(self.state["endpoint"]):
                    return self.state
                self.state.update(status="idle", endpoint=None)

            llm = self.cfg["llm"]
            mode = llm.get("mode", "auto")

            # 1) something already listening on the configured local endpoint
            if mode in ("auto", "bundled"):
                base = llm["base_url"].rstrip("/")
                ids = self._models(base)
                if ids is not None:
                    self.state.update(status="ready", mode="external", endpoint=base,
                                      model=ids[0] if ids else "(unknown)", backend="existing process",
                                      pid=None, error=None, started_at=util.now_iso(), load_seconds=0)
                    return self.state

            # 2) LM Studio / other external server
            if mode in ("auto", "external"):
                base = llm["external_url"].rstrip("/")
                ids = self._models(base)
                if ids is not None:
                    self.state.update(status="ready", mode="external", endpoint=base,
                                      model=ids[0] if ids else "(unknown)", backend="external server",
                                      pid=None, error=None, started_at=util.now_iso(), load_seconds=0)
                    return self.state
                if mode == "external":
                    self.state.update(status="error", mode="external", error=(
                        f"No OpenAI-compatible server at {base}. Start LM Studio's local server "
                        f"(Developer tab -> Start Server), or set llm.mode to \"bundled\"."))
                    return self.state

            # 3) bundled llama.cpp
            if not wait:
                t = threading.Thread(target=self._start_bundled, daemon=True)
                t.start()
                return self.state
            return self._start_bundled()

    def _start_bundled(self) -> dict:
        llm = self.cfg["llm"]
        model = paths.resolve(llm["model_file"])
        if not model.is_file():
            self.state.update(status="error", mode="bundled",
                              error=f"Model file not found: {model}. Run tools/fetch-model.ps1 or pick a model in Settings.")
            return self.state

        order = ["vulkan", "cpu"] if llm.get("backend", "auto") == "auto" else [llm["backend"]]
        last_err = None
        for backend in order:
            exe = paths.LLAMA_DIR / backend / "llama-server.exe"
            if not exe.is_file():
                last_err = f"{backend} runtime missing at {exe}"
                continue
            self.state.update(status="starting", mode="bundled", backend=backend,
                              model=model.name, error=None)
            t0 = time.time()
            ok, err = self._spawn(exe, model, backend)
            if ok:
                self.state.update(status="ready",
                                  load_seconds=round(time.time() - t0, 1),
                                  pid=self.proc.pid if self.proc else None,
                                  started_at=util.now_iso())
                print(f"[llm] ready on {self.state['endpoint']} ({backend}, {self.state['load_seconds']}s)")
                return self.state
            last_err = err
            self.state.update(status="error", error=err)
            print(f"[llm] {backend} failed: {err}")
        self.state.update(status="error", error=last_err or "no usable llama.cpp runtime found")
        return self.state

    def _spawn(self, exe: Path, model: Path, backend: str) -> tuple[bool, str]:
        llm = self.cfg["llm"]
        # Random loopback port: keeps the unauthenticated model API away from
        # predictable ports that any local web page could probe.
        port = self._free_port()
        base = f"http://127.0.0.1:{port}"
        app_origin = f"http://127.0.0.1:{int(self.cfg['server']['port'])}"
        cmd = [
            str(exe), "-m", str(model),
            "--host", "127.0.0.1", "--port", str(port),
            "-c", str(int(llm["ctx"])),
            "-ngl", str(int(llm["gpu_layers"])),
            "-t", str(int(llm["threads"])),
            "--no-webui", "--jinja", "--metrics",
            "--alias", "portable-ai",
            "--cors-origins", f"{app_origin},http://localhost:{int(self.cfg['server']['port'])}",
        ]
        if llm.get("flash_attn", True):
            cmd += ["-fa", "on"]
        if llm.get("quant_kv", True):
            cmd += ["--cache-type-k", "q8_0", "--cache-type-v", "q8_0"]

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(self.log_path, "w", encoding="utf-8", errors="replace")
        log.write("$ " + " ".join(cmd) + "\n\n")
        log.flush()
        try:
            self.proc = subprocess.Popen(cmd, cwd=str(exe.parent), stdout=log, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
        except Exception as exc:
            return False, f"could not launch {exe.name}: {exc}"

        deadline = time.time() + int(llm.get("startup_timeout", 180))
        self.state["endpoint"] = base
        while time.time() < deadline:
            if self.proc.poll() is not None:
                return False, f"{backend} exited with code {self.proc.returncode}. " + self._log_tail(self.log_path)
            if self._healthy(base) or self._models(base, 1.0) is not None:
                return True, ""
            time.sleep(0.6)
        self._kill(self.proc)
        return False, f"{backend} did not become ready within {llm.get('startup_timeout')}s. " + self._log_tail(self.log_path)

    @staticmethod
    def _log_tail(path: Path, n: int = 6) -> str:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            return " | ".join(lines[-n:])[:800]
        except Exception:
            return ""

    @staticmethod
    def _kill(proc: subprocess.Popen | None, timeout: float = 6.0) -> None:
        if not proc or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=timeout)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def stop(self) -> None:
        with self.lock:
            self._kill(self.proc)
            self._kill(self.embed_proc)
            self.proc = None
            self.embed_proc = None
            self._embed_port = None
            if self.state["mode"] == "bundled":
                self.state.update(status="stopped", endpoint=None, pid=None)
            self.embed_state.update(status="stopped", endpoint=None)

    def restart(self) -> dict:
        with self.lock:
            self.stop()
            self.state.update(status="idle", error=None, mode=None)
            return self.ensure_started(wait=True)

    # ---------------- embeddings ----------------
    def _embed_base(self) -> str:
        port = self._embed_port or int(self.cfg["embed"]["port"])
        return f"http://127.0.0.1:{port}"

    def ensure_embed(self) -> bool:
        if not self.cfg["embed"].get("enabled", True):
            return False
        with self.lock:
            if self._models(self._embed_base(), 1.0) is not None:
                self.embed_state.update(status="ready", endpoint=self._embed_base(), error=None)
                return True
            if self.embed_proc and self.embed_proc.poll() is None:
                for _ in range(120):
                    if self._models(self._embed_base(), 0.6) is not None:
                        self.embed_state.update(status="ready", endpoint=self._embed_base())
                        return True
                    time.sleep(0.5)
                return False
            model = paths.resolve(self.cfg["embed"]["model_file"])
            exe = paths.LLAMA_DIR / "vulkan" / "llama-server.exe"
            if not exe.is_file():
                exe = paths.LLAMA_DIR / "cpu" / "llama-server.exe"
            if not model.is_file() or not exe.is_file():
                self.embed_state.update(status="unavailable", error="embedding model or runtime missing")
                return False
            self.embed_log_path.parent.mkdir(parents=True, exist_ok=True)
            log = open(self.embed_log_path, "w", encoding="utf-8", errors="replace")
            self._embed_port = self._free_port()
            base = self._embed_base()
            app_port = int(self.cfg["server"]["port"])
            origins = f"http://127.0.0.1:{app_port},http://localhost:{app_port}"
            for flag in ("--embeddings", "--embedding"):
                cmd = [str(exe), "-m", str(model), "--host", "127.0.0.1",
                       "--port", str(self._embed_port), "-c", "2048",
                       "-ngl", "999", "-t", "4", "--no-webui", flag, "--pooling", "mean",
                       "--cors-origins", origins]
                log.write("$ " + " ".join(cmd) + "\n\n"); log.flush()
                try:
                    self.embed_proc = subprocess.Popen(cmd, cwd=str(exe.parent), stdout=log,
                                                       stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                                       creationflags=CREATE_NO_WINDOW)
                except Exception as exc:
                    self.embed_state.update(status="error", error=str(exc))
                    continue
                for _ in range(160):
                    if self.embed_proc.poll() is not None:
                        break
                    if self._models(base, 0.6) is not None:
                        self.embed_state.update(status="ready", endpoint=base, error=None)
                        print(f"[embed] ready on {base}")
                        return True
                    time.sleep(0.5)
                if self.embed_proc.poll() is None:
                    self._kill(self.embed_proc)
                self.embed_proc = None
            self.embed_state.update(status="error", error=self._log_tail(self.embed_log_path))
            return False

    def embed(self, texts: list[str], query: bool = False) -> list[list[float]] | None:
        if not self.ensure_embed():
            return None
        model = self.cfg["embed"]["model_file"].lower()
        prefix = ""
        if "nomic" in model:
            prefix = "search_query: " if query else "search_document: "
        out: list[list[float]] = []
        base = self._embed_base()
        for i in range(0, len(texts), 16):
            batch = [prefix + t[:8000] for t in texts[i:i + 16]]
            r = _http_json(base + "/v1/embeddings", {"input": batch, "model": "embed"}, timeout=120)
            data = sorted(r.get("data", []), key=lambda x: x.get("index", 0))
            out.extend([d["embedding"] for d in data])
        if len(out) != len(texts):
            raise RuntimeError(f"embedding count mismatch: {len(out)} != {len(texts)}")
        return out

    # ---------------- chat ----------------
    def chat_stream(self, messages: list[dict], temperature: float | None = None,
                    max_tokens: int | None = None):
        st = self.ensure_started(wait=True)
        if st["status"] != "ready":
            yield {"type": "error", "message": st.get("error") or "model is not ready"}
            return
        base = st["endpoint"].rstrip("/")
        payload = {
            "model": st.get("model") or "local",
            "messages": messages,
            "stream": True,
            "temperature": float(temperature if temperature is not None else self.cfg["llm"]["temperature"]),
            "max_tokens": int(max_tokens or self.cfg["llm"]["max_tokens"]),
            "stream_options": {"include_usage": True},
        }
        req = urllib.request.Request(base + "/v1/chat/completions",
                                     data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        usage, timings = {}, {}
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                for raw in r:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("error"):
                        yield {"type": "error", "message": str(obj["error"])[:500]}
                        return
                    if obj.get("usage"):
                        usage = obj["usage"]
                    if obj.get("timings"):
                        timings = obj["timings"]
                    for ch in obj.get("choices", []):
                        delta = ch.get("delta") or {}
                        if delta.get("reasoning_content"):
                            yield {"type": "reasoning", "text": delta["reasoning_content"]}
                        if delta.get("content"):
                            yield {"type": "token", "text": delta["content"]}
                        if ch.get("finish_reason"):
                            pass
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:600]
            except Exception:
                pass
            yield {"type": "error", "message": f"inference server returned HTTP {e.code}: {body}"}
            return
        except Exception as exc:
            yield {"type": "error", "message": f"{exc.__class__.__name__}: {exc}"}
            return

        stats = {
            "prompt_tokens": usage.get("prompt_tokens") or timings.get("prompt_n"),
            "completion_tokens": usage.get("completion_tokens") or timings.get("predicted_n"),
            "tokens_per_second": round(timings.get("predicted_per_second") or 0, 1) or None,
            "prompt_ms": round(timings.get("prompt_ms") or 0) or None,
        }
        yield {"type": "done", "stats": stats}

    # ---------------- introspection ----------------
    def list_models(self) -> list[dict]:
        out = []
        if not paths.MODELS_DIR.exists():
            return out
        for p in sorted(paths.MODELS_DIR.rglob("*.gguf")):
            try:
                out.append({"name": p.name, "path": paths.rel(p),
                            "size": p.stat().st_size, "size_h": util.human(p.stat().st_size),
                            "embedding": "embed" in str(p.parent).lower() or "embed" in p.name.lower()})
            except OSError:
                pass
        return out

    def status(self) -> dict:
        st = dict(self.state)
        st["embed"] = dict(self.embed_state)
        st["models"] = self.list_models()
        st["configured_model"] = self.cfg["llm"]["model_file"]
        return st

    @staticmethod
    def gpu_info() -> list[dict]:
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=4, creationflags=CREATE_NO_WINDOW)
            gpus = []
            for line in out.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 4:
                    gpus.append({"name": parts[0], "vram_total_mb": int(float(parts[1])),
                                 "vram_used_mb": int(float(parts[2])), "util": parts[3]})
            return gpus
        except Exception:
            return []

    @staticmethod
    def ram_info() -> dict:
        try:
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MEMORYSTATUSEX(); m.dwLength = ctypes.sizeof(m)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return {"total": util.human(m.ullTotalPhys), "free": util.human(m.ullAvailPhys),
                    "used_pct": int(m.dwMemoryLoad)}
        except Exception:
            return {}
