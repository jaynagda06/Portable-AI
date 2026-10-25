"""Portable AI local server (stdlib only).

Serves the dark web UI, proxies chat streaming from the local model, runs static
binary analysis, and exposes the document library. Binds to loopback by default.
"""
from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import os
import re
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core import analyze as analyzer
from core import config as config_mod
from core import extract, paths, util
from core.llm import Runtime
from core.rag import Library

APP_HEADER = "PortableAI"
MAX_JSON = 4 * 1024 * 1024

SYSTEM_PROMPT = """You are Portable AI, a local offline assistant running from the user's USB drive.

You help with:
1. Explaining static analysis of Windows executables and DLLs (headers, sections, imports, exports,
   resources, strings, entropy, capability indicators).
2. Answering questions about the user's indexed local documents, citing the provided sources.
3. General Windows, programming, and defensive security questions.

Rules:
- Be concise and concrete. Use Markdown. Short paragraphs and bullets over long essays.
- When a binary analysis dossier is supplied, tie each claim to specific evidence (section, import,
  string, header field). Separate "observed" from "inferred".
- Never present a heuristic as proof of malicious intent. State uncertainty plainly.
- If the dossier lists no "attention" indicators, say the static profile looks ordinary and do not
  invent suspicious findings. Signed status and missing signatures are facts, not verdicts.
- You cannot execute files, open URLs, or reach the internet. For behaviour questions, propose a
  dynamic-analysis step in an isolated VM instead of guessing.
- Refuse help with bypassing exam/proctoring software, DRM, or accessing systems without authorization.
  Offer a legitimate alternative when possible.
- If the user asks about something outside the provided evidence, say what you would need."""


class App:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.runtime = Runtime(cfg)
        self.library = Library(cfg, embedder=self.runtime.embed)
        self.started = time.time()

    def close(self):
        self.runtime.stop()


class Handler(BaseHTTPRequestHandler):
    server_version = "PortableAI/1.0"
    protocol_version = "HTTP/1.0"

    # ---------- plumbing ----------
    @property
    def app(self) -> App:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, fmt, *args):
        if "/api/status" in (args[0] if args else ""):
            return
        print(f"[http] {self.address_string()} {fmt % args}")

    def _send(self, code: int, body: bytes, ctype="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _err(self, msg, code=400):
        self._json({"ok": False, "error": msg}, code)

    def _body(self, limit=MAX_JSON) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ValueError(f"body too large ({n} bytes)")
        return self.rfile.read(n) if n else b""

    def _json_body(self) -> dict:
        raw = self._body()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            raise ValueError("invalid JSON body")

    # ---------- security ----------
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        hostname = host.split(":")[0].strip("[]")
        if self.app.cfg["server"].get("lan"):
            return True  # token is the control; LAN hostnames vary
        return hostname in ("127.0.0.1", "localhost", "::1", "")

    def _token_ok(self) -> bool:
        cfg = self.app.cfg["server"]
        if not cfg.get("lan"):
            return True
        want = str(cfg.get("lan_token") or "")
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        supplied = (self.headers.get("X-Auth-Token")
                    or (q.get("t") or [""])[0]
                    or (self._cookie().get("pai_token") or ""))
        return bool(want) and hmac.compare_digest(str(supplied), want)

    def _cookie(self) -> dict:
        raw = self.headers.get("Cookie") or ""
        out = {}
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    def _authorized(self) -> bool:
        return self._token_ok()

    def _csrf_ok(self) -> bool:
        # custom header cannot be set by a cross-origin form/navigation
        return (self.headers.get("X-Requested-With") or "") == APP_HEADER

    # ---------- routing ----------
    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method: str):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        q = urllib.parse.parse_qs(parsed.query)
        try:
            if not self._host_ok():
                return self._err("invalid Host header (DNS-rebinding protection)", 403)

            if route == "/" or route == "/index.html":
                if self.app.cfg["server"].get("lan"):
                    if not self._token_ok():
                        return self._send(401, _LOGIN_HTML.encode("utf-8"), "text/html; charset=utf-8")
                    tok = (q.get("t") or [""])[0]
                    extra = {"Set-Cookie": f"pai_token={tok}; Path=/; HttpOnly; SameSite=Lax"} if tok else {}
                    return self._file(paths.WEB_DIR / "index.html", "text/html; charset=utf-8",
                                      extra=extra, cache=False)
                return self._file(paths.WEB_DIR / "index.html", "text/html; charset=utf-8", cache=False)

            if route.startswith("/static/"):
                if not self._authorized():
                    return self._err("unauthorized", 401)
                name = route[len("/static/"):]
                if not re.fullmatch(r"[A-Za-z0-9_.\-]+", name):
                    return self._err("bad asset name", 400)
                ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
                return self._file(paths.WEB_DIR / name, ctype)

            if route.startswith("/api/"):
                if not self._authorized():
                    return self._err("unauthorized", 401)
                if method == "POST" and not self._csrf_ok():
                    return self._err("missing X-Requested-With header", 403)
                return self._api(route, q, method)
            return self._err("not found", 404)
        except BrokenPipeError:
            pass
        except ValueError as e:
            self._err(str(e), 400)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._err(f"{e.__class__.__name__}: {e}", 500)

    def _file(self, path: Path, ctype=None, extra=None, cache=True):
        try:
            path = path.resolve()
            if not path.is_file() or not path.is_relative_to(paths.WEB_DIR.resolve()):
                return self._err("not found", 404)
        except Exception:
            return self._err("not found", 404)
        ctype = ctype or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        hdrs = {"Cache-Control": "public, max-age=3600"} if cache else {"Cache-Control": "no-store"}
        hdrs.update(extra or {})
        self._send(200, path.read_bytes(), ctype, hdrs)

    # ---------- API ----------
    def _api(self, route: str, q: dict, method: str):
        app = self.app
        cfg = app.cfg

        if route == "/api/health":
            return self._json({"ok": True, "uptime": round(time.time() - app.started, 1)})

        if route == "/api/status":
            st = app.runtime.status()
            st["ok"] = True
            st["gpu"] = app.runtime.gpu_info()
            st["ram"] = app.runtime.ram_info()
            st["library"] = app.library.stats()
            st["storage"] = util.disk_info()
            st["lan"] = {"enabled": bool(cfg["server"]["lan"]),
                         "url": f"http://{util.get_lan_ip()}:{cfg['server']['port']}",
                         "token": cfg["server"]["lan_token"] if cfg["server"]["lan"] else ""}
            return self._json(st)

        if route == "/api/config":
            if method == "GET":
                safe = json.loads(json.dumps(cfg))
                return self._json({"ok": True, "config": safe})
            body = self._json_body()
            restart = False
            for section in ("llm", "embed", "server", "rag"):
                if isinstance(body.get(section), dict):
                    for k, v in body[section].items():
                        if k in cfg[section]:
                            cfg[section][k] = v
                            if section in ("llm", "embed"):
                                restart = True
            config_mod.save(cfg)
            if restart:
                threading.Thread(target=app.runtime.restart, daemon=True).start()
                if cfg["embed"].get("enabled"):
                    threading.Thread(target=app.runtime.ensure_embed, daemon=True).start()
            return self._json({"ok": True, "restarting": restart, "config": cfg})

        if route == "/api/runtime/restart" and method == "POST":
            def go():
                app.runtime.restart()
                app.runtime.ensure_embed()
            threading.Thread(target=go, daemon=True).start()
            return self._json({"ok": True, "message": "restarting model"})

        if route == "/api/runtime/stop" and method == "POST":
            app.runtime.stop()
            return self._json({"ok": True})

        if route == "/api/models":
            return self._json({"ok": True, "models": app.runtime.list_models()})

        if route == "/api/chat" and method == "POST":
            return self._chat()

        if route == "/api/analyze" and method == "POST":
            return self._analyze(q)

        if route == "/api/reports":
            idx = util.read_json(paths.REPORTS_DIR / "_index.json", []) or []
            return self._json({"ok": True, "reports": idx})

        if route.startswith("/api/reports/"):
            rid = route.rsplit("/", 1)[-1]
            if not re.fullmatch(r"[0-9a-f]{16}", rid):
                return self._err("bad report id", 400)
            rep = util.read_json(paths.REPORTS_DIR / f"{rid}.json")
            if not rep:
                return self._err("report not found", 404)
            return self._json({"ok": True, "report": rep})

        if route == "/api/library" and method == "GET":
            return self._json({"ok": True, "docs": app.library.list_docs(), "stats": app.library.stats()})

        if route == "/api/library/index" and method == "POST":
            body = self._json_body()
            target = (body.get("path") or "").strip().strip('"')
            if not target:
                return self._err("path required")
            p = Path(os.path.expandvars(os.path.expanduser(target)))
            if p.is_file():
                r = app.library.add_file(p, force=bool(body.get("force")))
                return self._json(r)
            if p.is_dir():
                r = app.library.index_folder(str(p))
                return self._json(r)
            return self._err(f"path not found: {p}", 404)

        if route == "/api/library/upload" and method == "POST":
            name = (q.get("name") or ["upload.bin"])[0]
            return self._library_upload(name)

        if route == "/api/library/delete" and method == "POST":
            body = self._json_body()
            return self._json(app.library.remove(int(body.get("id"))))

        if route == "/api/library/search" and method == "GET":
            query = (q.get("q") or [""])[0]
            k = int((q.get("k") or ["6"])[0])
            return self._json(app.library.search(query, k))

        if route == "/api/library/reembed" and method == "POST":
            docs = app.library.list_docs()
            def go():
                for d in docs:
                    try:
                        app.library.add_file(d["path"], force=True)
                    except Exception as e:
                        print(f"[library] re-embed failed for {d['path']}: {e}")
            threading.Thread(target=go, daemon=True).start()
            return self._json({"ok": True, "message": f"re-embedding {len(docs)} documents"})

        return self._err("not found", 404)

    # ---------- chat ----------
    def _chat(self):
        body = self._json_body()
        history = body.get("messages") or []
        if not isinstance(history, list) or not history:
            return self._err("messages required")
        use_library = bool(body.get("use_library"))
        report_id = (body.get("report_id") or "").strip()

        ctx_blocks = []
        sources = []
        if use_library:
            last_user = next((m.get("content", "") for m in reversed(history) if m.get("role") == "user"), "")
            try:
                res = self.app.library.search(last_user, int(self.app.cfg["rag"]["top_k"]))
                for i, r in enumerate(res.get("results", []), 1):
                    ctx_blocks.append(
                        f"[S{i}] {r['name']}" + (f" (page {r['page']})" if r.get("page") else "") +
                        f"\n{r['text'][:1800]}")
                    sources.append({"n": i, "name": r["name"], "page": r.get("page"),
                                    "snippet": r["snippet"], "path": r["path"]})
            except Exception as e:
                print(f"[chat] library search failed: {e}")

        if report_id:
            if not re.fullmatch(r"[0-9a-f]{16}", report_id):
                return self._err("bad report id")
            rep = util.read_json(paths.REPORTS_DIR / f"{report_id}.json")
            if rep:
                ctx_blocks.append("BINARY ANALYSIS DOSSIER (static, read-only):\n" + rep.get("dossier", ""))

        system = SYSTEM_PROMPT
        if ctx_blocks:
            system += ("\n\n=== CONTEXT ===\n" +
                       "\n\n".join(ctx_blocks) +
                       "\n=== END CONTEXT ===\n")
            if sources:
                system += ("Cite the document sources as [S1], [S2] when you use them. "
                           "Do not invent citations.")
            else:
                system += ("This context is a static-analysis dossier. Refer to evidence by field "
                           "name (for example 'section .rsrc', 'import WriteProcessMemory', "
                           "'compile timestamp'). Do not use [S1] style markers.")

        msgs = [{"role": "system", "content": system}]
        total = 0
        for m in history[-14:]:
            role = m.get("role")
            content = (m.get("content") or "").strip()
            if role not in ("user", "assistant") or not content:
                continue
            content = content[:6000]
            total += len(content)
            if total > 26000:
                break
            msgs.append({"role": role, "content": content})
        if len(msgs) == 1:
            return self._err("no usable user message")

        # SSE
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True

        def emit(obj):
            self.wfile.write(b"data: " + json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n\n")
            self.wfile.flush()

        if sources:
            emit({"type": "sources", "sources": sources})
        st = self.app.runtime.state
        emit({"type": "meta", "model": st.get("model"), "mode": st.get("mode"),
              "backend": st.get("backend")})
        try:
            for ev in self.app.runtime.chat_stream(msgs, body.get("temperature"), body.get("max_tokens")):
                emit(ev)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as e:
            try:
                emit({"type": "error", "message": f"{e.__class__.__name__}: {e}"})
            except Exception:
                pass

    # ---------- analysis ----------
    def _analyze(self, q: dict):
        cfg = self.app.cfg
        ctype = (self.headers.get("Content-Type") or "").lower()
        if ctype.startswith("application/json"):
            body = self._json_body()
            target = (body.get("path") or "").strip().strip('"')
            if not target:
                return self._err("path required")
            p = Path(os.path.expandvars(os.path.expanduser(target)))
            if not p.is_file():
                return self._err(f"file not found: {p}", 404)
            try:
                rep = analyzer.analyze(p, cfg, save=True)
            except Exception as e:
                return self._err(f"analysis failed: {e}", 500)
            return self._json({"ok": True, "report": rep})

        # raw upload body
        name = (q.get("name") or ["upload.bin"])[0]
        name = re.sub(r"[^A-Za-z0-9._\- ]+", "_", Path(name).name)[:120] or "upload.bin"
        max_bytes = int(cfg["analysis"]["max_upload_mb"]) * 1024 * 1024
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return self._err("empty upload")
        if n > max_bytes:
            return self._err(f"file exceeds {cfg['analysis']['max_upload_mb']} MB upload limit", 413)
        dest = paths.UPLOADS_DIR / f"{int(time.time())}_{name}"
        remaining = n
        try:
            with open(dest, "wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 256, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
            rep = analyzer.analyze(dest, cfg, save=True)
            rep["file"]["name"] = name
            rep["summary"]["name"] = name
            util.write_json(paths.REPORTS_DIR / f"{rep['id']}.json", rep)
            idx_file = paths.REPORTS_DIR / "_index.json"
            idx = util.read_json(idx_file, []) or []
            for item in idx:
                if item.get("id") == rep["id"]:
                    item["name"] = name
            util.write_json(idx_file, idx)
            return self._json({"ok": True, "report": rep})
        except Exception as e:
            return self._err(f"analysis failed: {e}", 500)
        finally:
            try:
                dest.unlink()
            except OSError:
                pass

    def _library_upload(self, name: str):
        name = re.sub(r"[^A-Za-z0-9._\- ]+", "_", Path(name).name)[:160] or "document.txt"
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return self._err("empty upload")
        limit = int(self.app.cfg["rag"]["max_file_mb"]) * 1024 * 1024
        if n > limit:
            return self._err(f"file exceeds {self.app.cfg['rag']['max_file_mb']} MB limit", 413)
        dest = paths.UPLOADS_DIR / f"{int(time.time())}_{name}"
        try:
            remaining = n
            with open(dest, "wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 256, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
            r = self.app.library.add_file(dest)
            if r.get("ok"):
                # keep the copy: the library entry points at this stable path
                return self._json(r)
            return self._json(r, 400)
        except Exception as e:
            return self._err(f"index failed: {e}", 500)


_LOGIN_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Portable AI - token required</title>
<style>body{background:#0b0d10;color:#e6e9ef;font-family:Segoe UI,system-ui,sans-serif;display:grid;
place-items:center;height:100vh;margin:0}form{background:#12161c;border:1px solid #232a35;padding:28px;border-radius:14px;width:320px}
h1{font-size:16px;margin:0 0 14px}input{width:100%;padding:10px;border-radius:8px;border:1px solid #2a3341;
background:#0e1218;color:#e6e9ef;box-sizing:border-box}button{margin-top:12px;width:100%;padding:10px;border-radius:8px;
border:0;background:#5b8cff;color:#fff;font-weight:600;cursor:pointer}</style></head><body>
<form method="get" action="/"><h1>Portable AI access token</h1>
<input name="t" placeholder="Paste the token shown on the host" autofocus autocomplete="off">
<button>Unlock</button></form></body></html>"""


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def run_self_check() -> int:
    """Offline installation check. Starts no model and opens no sockets."""
    import platform
    import shutil
    import sqlite3
    import subprocess as sp
    import tempfile
    from importlib.metadata import PackageNotFoundError, version

    ok = True
    print("Portable AI - installation check")
    print("=" * 62)
    print(f"Project root : {paths.ROOT}")
    print(f"Python       : {platform.python_version()} ({platform.architecture()[0]})")
    if sys.version_info < (3, 10):
        print("  FAIL: Python 3.10 or newer is required")
        ok = False

    for pkg in ("pefile", "pypdf", "capstone"):
        try:
            print(f"  {pkg:<12}: {version(pkg)}")
        except PackageNotFoundError:
            print(f"  {pkg:<12}: MISSING")
            ok = False

    cfg = config_mod.load()
    print(f"Config       : {paths.CONFIG_FILE}")
    print(f"  mode       : {cfg['llm']['mode']} / backend {cfg['llm']['backend']} / ctx {cfg['llm']['ctx']}")

    main_model = paths.ROOT / cfg["llm"]["model_file"]
    embed_model = paths.ROOT / cfg["embed"]["model_file"]
    for label, p, minimum in (("main model", main_model, 500 * 1024 ** 2),
                              ("embed model", embed_model, 20 * 1024 ** 2)):
        if p.is_file():
            mb = p.stat().st_size / 1024 ** 2
            print(f"{label:<12} : {mb:,.0f} MB  {p}")
            if p.stat().st_size < minimum:
                print(f"  WARN: {label} looks too small")
        else:
            print(f"{label:<12} : MISSING  {p}")
            if label == "main model":
                ok = False

    llama = paths.ROOT / "runtime" / "llama"
    vulkan_ok = (llama / "vulkan" / "llama-server.exe").is_file()
    cpu_ok = (llama / "cpu" / "llama-server.exe").is_file()
    print(f"llama.cpp vulkan: {'ok' if vulkan_ok else 'MISSING'}  {llama / 'vulkan' / 'llama-server.exe'}")
    print(f"llama.cpp cpu   : {'ok' if cpu_ok else 'MISSING'}  {llama / 'cpu' / 'llama-server.exe'}")
    if not vulkan_ok:
        print("  note: will fall back to CPU" if cpu_ok else "  FAIL: no llama-server binary")
    if not (vulkan_ok or cpu_ok):
        ok = False

    # writable data dir
    try:
        with tempfile.NamedTemporaryFile(dir=paths.DATA_DIR, prefix=".check-", delete=True) as f:
            f.write(b"ok")
        print(f"Data dir     : writable  {paths.DATA_DIR}")
    except Exception as e:
        print(f"Data dir     : NOT WRITABLE ({e})")
        ok = False

    db = paths.INDEX_DIR / "library.db"
    try:
        con = sqlite3.connect(db)
        status = con.execute("PRAGMA quick_check").fetchone()[0]
        docs = con.execute("SELECT count(*) FROM docs").fetchone()[0]
        con.close()
        print(f"Library DB   : {status}, {docs} document(s)")
    except sqlite3.OperationalError as e:
        print(f"Library DB   : empty/new ({e})")
    except Exception as e:
        print(f"Library DB   : ERROR ({e})")
        ok = False

    # Any existing OpenAI-compatible server? Informational only.
    try:
        import urllib.request
        with urllib.request.urlopen(cfg["llm"]["external_url"].rstrip("/") + "/v1/models", timeout=1.5) as r:
            if r.status == 200:
                print(f"External LLM : found at {cfg['llm']['external_url']}")
    except Exception:
        print("External LLM : none detected (bundled llama.cpp will be used)")

    try:
        smi = sp.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                     capture_output=True, text=True, timeout=8, creationflags=0x08000000)
        if smi.returncode == 0 and smi.stdout.strip():
            print(f"NVIDIA GPU   : {smi.stdout.strip().splitlines()[0]}")
        else:
            print("NVIDIA GPU   : not detected (Vulkan/CPU may still work)")
    except Exception:
        print("NVIDIA GPU   : not detected")

    free = shutil.disk_usage(paths.ROOT).free / 1024 ** 3
    print(f"Disk free    : {free:,.1f} GB on the drive holding the project")
    if free < 2:
        print("  WARN: low free space for library uploads and reports")

    print("=" * 62)
    print("RESULT:", "ready to run" if ok else "problems found (see FAIL above)")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Portable AI server")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--lan", action="store_true", help="listen on all interfaces (token required)")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--check", action="store_true", help="verify the installation and exit")
    args = ap.parse_args()

    paths.ensure_dirs()
    if args.check:
        sys.exit(run_self_check())
    cfg = config_mod.load()
    if args.lan:
        cfg["server"]["lan"] = True
        config_mod.save(cfg)
    host = args.host or ("0.0.0.0" if cfg["server"]["lan"] else cfg["server"]["host"])
    port = args.port or int(cfg["server"]["port"])
    cfg["server"]["port"] = port

    app = App(cfg)
    server = Server((host, port), Handler)
    server.app = app  # type: ignore[attr-defined]
    try:
        (paths.DATA_DIR / "server.pid").write_text(str(os.getpid()), encoding="ascii")
    except OSError:
        pass

    url = f"http://127.0.0.1:{port}"
    print("=" * 62)
    print("  Portable AI  -  local, offline analyst workstation")
    print("=" * 62)
    print(f"  UI        : {url}")
    if cfg["server"]["lan"]:
        print(f"  LAN       : http://{util.get_lan_ip()}:{port}/?t={cfg['server']['lan_token']}")
    print(f"  Model     : {cfg['llm']['model_file']}")
    print(f"  Mode      : {cfg['llm']['mode']} (bundled llama.cpp fallback)")
    print(f"  Library   : {paths.INDEX_DIR / 'library.db'}")
    print("=" * 62)
    print("  Ctrl+C to stop")

    def warm():
        st = app.runtime.ensure_started(wait=True)
        if st["status"] == "ready":
            print(f"[llm] model ready ({st['mode']}, {st['backend']}), endpoint {st['endpoint']}")
        else:
            print(f"[llm] not ready: {st.get('error')}")
        if cfg["embed"].get("enabled"):
            app.runtime.ensure_embed()
    threading.Thread(target=warm, daemon=True).start()

    if cfg["server"].get("open_browser", True) and not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[app] shutting down")
    finally:
        server.server_close()
        app.close()


if __name__ == "__main__":
    main()
