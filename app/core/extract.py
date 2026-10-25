"""Text extraction for documents, using only bundled libraries.

Supported: PDF (pypdf), DOCX/PPTX/XLSX (zip + XML, stdlib), HTML, and any
plain-text-ish file (source code, logs, notes, markdown, CSV, JSON...).
Returns a list of blocks: {"text": str, "page": int|None}.
"""
from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path

TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".json", ".jsonl", ".csv", ".tsv",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".xml", ".sln", ".csproj",
    ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".cs", ".c", ".h", ".hpp", ".cpp",
    ".cc", ".java", ".go", ".rs", ".rb", ".php", ".ps1", ".bat", ".cmd", ".sh",
    ".sql", ".html", ".htm", ".css", ".vue", ".lua", ".swift", ".kt", ".gradle",
    ".cmake", ".make", ".dockerfile", ".env", ".gitignore", ".editorconfig",
}
OFFICE_EXTS = {".docx", ".pptx", ".xlsx"}
PDF_EXTS = {".pdf"}
SUPPORTED = TEXT_EXTS | OFFICE_EXTS | PDF_EXTS

_SKIP_DIRS = {
    "$recycle.bin", "system volume information", "windows", "node_modules", ".git",
    "__pycache__", "appdata", "programdata", ".venv", "venv", "site-packages",
    "windows.old", "$windows.~bt", "$windows.~ws", "recovery",
}


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED or path.name.lower() in ("makefile", "dockerfile")


def should_skip_dir(name: str) -> bool:
    n = name.lower()
    return n in _SKIP_DIRS or n.startswith(".") or n.startswith("$")


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _pdf(path: Path) -> list[dict]:
    from pypdf import PdfReader
    reader = PdfReader(str(path), strict=False)
    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception as exc:
            raise ValueError(f"PDF is encrypted ({exc.__class__.__name__})") from exc
    blocks = []
    for i, page in enumerate(reader.pages, 1):
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""
        txt = txt.strip()
        if txt:
            blocks.append({"text": txt, "page": i})
    return blocks


def _office(path: Path) -> list[dict]:
    """DOCX/PPTX/XLSX -> text using only zipfile + regex (no python-docx)."""
    ext = path.suffix.lower()
    out: list[str] = []
    with zipfile.ZipFile(str(path)) as z:
        names = z.namelist()
        if ext == ".docx":
            parts = [n for n in names if re.fullmatch(r"word/(document|header\d*|footer\d*)\.xml", n)]
        elif ext == ".pptx":
            parts = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                           key=lambda s: int(re.search(r"(\d+)", s.split("/")[-1]).group(1)))
        else:  # xlsx
            parts = sorted(n for n in names if n.startswith("xl/worksheets/sheet"))
        for name in parts:
            xml = _decode(z.read(name))
            xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
            xml = re.sub(r"</w:p>|</a:p>", "\n", xml)
            xml = re.sub(r"<[^>]+>", "", xml)
            txt = html.unescape(xml).strip()
            if txt:
                out.append(txt)
    if not out:
        return []
    return [{"text": "\n\n".join(out), "page": None}]


def _html(path: Path) -> list[dict]:
    raw = _decode(path.read_bytes())
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h[1-6]>|</li>", "\n", raw)
    txt = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    txt = re.sub(r"[ \t]{2,}", " ", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    return [{"text": txt, "page": None}] if txt else []


def extract(path: str | Path, max_chars: int = 4_000_000) -> list[dict]:
    path = Path(path)
    ext = path.suffix.lower()
    if ext in PDF_EXTS:
        blocks = _pdf(path)
    elif ext in OFFICE_EXTS:
        blocks = _office(path)
    elif ext in (".html", ".htm"):
        blocks = _html(path)
    elif ext in TEXT_EXTS or path.name.lower() in ("makefile", "dockerfile"):
        blocks = [{"text": _decode(path.read_bytes()), "page": None}]
    else:
        raise ValueError(f"unsupported file type: {ext or '(none)'}")

    # hard cap so a pathological file cannot exhaust memory
    total, kept = 0, []
    for b in blocks:
        t = re.sub(r"\x00", " ", b["text"]).strip()
        if not t:
            continue
        if total + len(t) > max_chars:
            t = t[: max(0, max_chars - total)]
        if t:
            kept.append({"text": t, "page": b.get("page")})
            total += len(t)
        if total >= max_chars:
            break
    return kept
