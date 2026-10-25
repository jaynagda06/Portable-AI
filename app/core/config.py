"""Configuration: defaults, deep-merge, save, and first-run token generation."""
from __future__ import annotations

import copy
import secrets

from . import paths, util

DEFAULTS = {
    "server": {
        "host": "127.0.0.1",
        "port": 8099,
        "lan": False,
        "lan_token": "",
        "open_browser": True,
    },
    "llm": {
        # auto  = use an already-running OpenAI-compatible server if reachable,
        #         otherwise start the bundled llama.cpp server.
        # bundled / external force one path.
        "mode": "auto",
        "base_url": "http://127.0.0.1:8080",
        "external_url": "http://127.0.0.1:1234",
        "model_file": "models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        "backend": "auto",          # auto | vulkan | cpu
        "ctx": 8192,
        "gpu_layers": 999,
        "threads": 8,
        "flash_attn": True,
        "quant_kv": False,
        "temperature": 0.3,
        "max_tokens": 1024,
        # Per backend. A cold 2.4 GB model read from a USB 2.0 stick measured
        # ~76 s, and slower sticks take longer; keep plenty of headroom.
        "startup_timeout": 300,
    },
    "embed": {
        "enabled": True,
        "port": 8081,
        "model_file": "models/embeddings/nomic-embed-text-v1.5.Q4_K_M.gguf",
    },
    "rag": {
        "chunk_chars": 1400,
        "overlap": 200,
        "top_k": 6,
        "max_file_mb": 100,
    },
    "analysis": {
        "max_strings": 4000,
        "disasm_count": 40,
        "max_upload_mb": 512,
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    paths.ensure_dirs()
    cfg = _merge(DEFAULTS, util.read_json(paths.CONFIG_FILE, {}) or {})
    changed = False
    if not cfg["server"].get("lan_token"):
        cfg["server"]["lan_token"] = secrets.token_urlsafe(24)
        changed = True
    if changed:
        save(cfg)
    return cfg


def save(cfg: dict) -> None:
    # basic sanitation so a hand-edited config cannot silently break the app
    s, l = cfg.get("server", {}), cfg.get("llm", {})
    s["port"] = max(1024, min(65535, int(s.get("port", 8099))))
    l["ctx"] = max(1024, min(65536, int(l.get("ctx", 8192))))
    l["gpu_layers"] = max(0, min(999, int(l.get("gpu_layers", 999))))
    l["threads"] = max(1, min(64, int(l.get("threads", 8))))
    util.write_json(paths.CONFIG_FILE, cfg)
