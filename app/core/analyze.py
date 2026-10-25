"""Static, read-only analysis of Windows PE files and arbitrary binaries.

Design rules:
  * The target file is only ever OPENED FOR READING. Nothing is executed,
    loaded, or written next to it.
  * Findings are reported as indicators with evidence, never as a verdict.
  * Everything degrades gracefully: a malformed PE yields a partial report.
"""
from __future__ import annotations

import hashlib
import math
import re
import time
from collections import Counter
from pathlib import Path

from . import paths, util

try:
    import pefile  # type: ignore
except Exception:  # pragma: no cover
    pefile = None

try:
    import capstone  # type: ignore
    _CS_AVAILABLE = True
except Exception:  # pragma: no cover
    _CS_AVAILABLE = False

MAX_STRINGS_SCAN = 128 * 1024 * 1024
MAX_STRING_ITEMS = 100_000

MACHINES = {
    0x014C: "x86 (i386)", 0x8664: "x64 (AMD64)", 0x01C0: "ARM", 0x01C4: "ARMv7",
    0xAA64: "ARM64", 0x0200: "IA64", 0x01F0: "PowerPC",
}
SUBSYSTEMS = {1: "NATIVE", 2: "WINDOWS_GUI", 3: "WINDOWS_CUI", 5: "OS2_CUI",
              7: "POSIX_CUI", 9: "WINDOWS_CE", 10: "EFI_APPLICATION",
              13: "XBOX", 14: "WINDOWS_BOOT"}
DLL_CHARS = {
    0x0020: "HIGH_ENTROPY_VA", 0x0040: "DYNAMIC_BASE (ASLR)", 0x0080: "FORCE_INTEGRITY",
    0x0100: "NX_COMPAT (DEP)", 0x0200: "NO_ISOLATION", 0x0400: "NO_SEH",
    0x0800: "NO_BIND", 0x1000: "APPCONTAINER", 0x2000: "WDM_DRIVER",
    0x4000: "GUARD_CF (CFG)", 0x8000: "TERMINAL_SERVER_AWARE",
}
FILE_CHARS = {
    0x0001: "RELOCS_STRIPPED", 0x0002: "EXECUTABLE_IMAGE", 0x0020: "LARGE_ADDRESS_AWARE",
    0x0100: "32BIT_MACHINE", 0x0200: "DEBUG_STRIPPED", 0x1000: "SYSTEM",
    0x2000: "DLL", 0x4000: "UP_SYSTEM_ONLY",
}
SEC_CHARS = {
    0x00000020: "CODE", 0x00000040: "INITIALIZED_DATA", 0x00000080: "UNINITIALIZED_DATA",
    0x02000000: "DISCARDABLE", 0x10000000: "SHARED",
    0x20000000: "EXECUTE", 0x40000000: "READ", 0x80000000: "WRITE",
}
RES_TYPES = {
    1: "CURSOR", 2: "BITMAP", 3: "ICON", 4: "MENU", 5: "DIALOG", 6: "STRING",
    7: "FONTDIR", 8: "FONT", 9: "ACCELERATOR", 10: "RCDATA", 11: "MESSAGETABLE",
    12: "GROUP_CURSOR", 14: "GROUP_ICON", 16: "VERSION", 17: "DLGINCLUDE",
    19: "PLUGPLAY", 20: "VXD", 21: "ANICURSOR", 22: "ANIICON", 23: "HTML",
    24: "MANIFEST",
}

API_CATEGORIES = {
    "process injection / memory manipulation": [
        "VirtualAllocEx", "WriteProcessMemory", "CreateRemoteThread", "NtCreateThreadEx",
        "RtlCreateUserThread", "QueueUserAPC", "SetThreadContext", "NtUnmapViewOfSection",
        "VirtualProtectEx", "NtMapViewOfSection", "NtWriteVirtualMemory",
    ],
    "debugger / analysis detection": [
        "IsDebuggerPresent", "CheckRemoteDebuggerPresent", "NtQueryInformationProcess",
        "NtSetInformationThread", "OutputDebugStringA", "OutputDebugStringW",
        "NtQuerySystemInformation",
    ],
    "autostart / persistence": [
        "RegSetValueExA", "RegSetValueExW", "RegCreateKeyExA", "RegCreateKeyExW",
        "CreateServiceA", "CreateServiceW", "StartServiceA", "StartServiceW",
        "NtLoadDriver", "ChangeServiceConfigA", "ChangeServiceConfigW",
    ],
    "network communication": [
        "InternetOpenA", "InternetOpenW", "InternetConnectA", "InternetConnectW",
        "HttpSendRequestA", "HttpSendRequestW", "WinHttpOpen", "WinHttpSendRequest",
        "WSAStartup", "WSASocketW", "connect", "send", "recv", "getaddrinfo",
        "URLDownloadToFileA", "URLDownloadToFileW", "InternetReadFile",
    ],
    "cryptography": [
        "CryptEncrypt", "CryptDecrypt", "CryptGenKey", "CryptAcquireContextA",
        "CryptAcquireContextW", "BCryptEncrypt", "BCryptDecrypt",
        "BCryptGenerateSymmetricKey", "CryptStringToBinaryA", "CryptStringToBinaryW",
    ],
    "keystroke / input capture": [
        "SetWindowsHookExA", "SetWindowsHookExW", "GetAsyncKeyState", "GetKeyState",
        "GetKeyboardState", "RegisterRawInputDevices", "GetForegroundWindow",
    ],
    "screen capture": [
        "GetDC", "BitBlt", "CreateCompatibleDC", "CreateCompatibleBitmap",
        "GetSystemMetrics", "PrintWindow", "StretchBlt",
    ],
    "synthetic input": ["SendInput", "keybd_event", "mouse_event"],
    "privilege manipulation": [
        "OpenProcessToken", "AdjustTokenPrivileges", "LookupPrivilegeValueA",
        "LookupPrivilegeValueW", "DuplicateTokenEx", "ImpersonateLoggedOnUser",
    ],
    "process execution": [
        "CreateProcessA", "CreateProcessW", "ShellExecuteA", "ShellExecuteW",
        "WinExec", "system", "CreateProcessAsUserA", "CreateProcessAsUserW",
    ],
}

_URL_RE = re.compile(r"https?://[^\s\"'<>\x00]{4,200}", re.I)
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")
_REG_RE = re.compile(r"(?i)\b(?:HKLM|HKCU|HKCR|HKU|HKCC)\\[^\s\"'<>\x00]{2,200}")
_WINPATH_RE = re.compile(r"(?i)\b[A-Za-z]:\\[^\s\"'<>\x00]{3,200}")
_DOMAIN_RE = re.compile(r"(?i)\b[a-z0-9][a-z0-9\-]{1,60}\.(?:com|net|org|io|ru|cn|xyz|top|info|biz|onion|su|cc|tk)\b")
_URLSAFE_RE = re.compile(r"^[A-Za-z0-9+/=]{40,}$")

_KEYWORDS = [
    "password", "passwd", "token", "api_key", "apikey", "secret", "license", "serial",
    "decrypt", "encrypt", "payload", "inject", "keylog", "screenshot", "backdoor",
    "schtasks", "powershell -enc", "certutil", "bitsadmin", "mshta", "wscript.shell",
    "discord.com/api/webhooks", "telegram", "bitcoin", "wallet", "private key",
    "BEGIN RSA", "ssh-rsa", "AKIA", "ghp_", "AIza",
]
_NOTABLE_HINTS = ("User-Agent", "Mozilla/", "HTTP/1.1", "SELECT ", "INSERT INTO",
                  ".dll", ".exe", "SOFTWARE\\", "SYSTEM\\", "\\CurrentVersion\\Run")


def _sha(fn: str, path: Path) -> str:
    h = hashlib.new(fn)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_kind(data: bytes) -> str:
    if data[:2] == b"MZ":
        return "Windows PE"
    if data[:4] == b"\x7fELF":
        return "ELF"
    if data[:4] in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"):
        return "Mach-O"
    if data[:5] == b"%PDF-":
        return "PDF"
    if data[:4] == b"PK\x03\x04":
        return "ZIP container (jar/apk/docx/zip)"
    if data[:4] == b"Rar!":
        return "RAR archive"
    if data[:6] == b"7z\xbc\xaf\x27\x1c":
        return "7z archive"
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "OLE compound (legacy Office / MSI)"
    if data[:4] == b"\x1f\x8b\x08\x00":
        return "gzip"
    return "unknown"


def _rva_to_offset(pe, rva: int):
    try:
        return pe.get_offset_from_rva(rva)
    except Exception:
        return None


def _mask(value: int, table: dict) -> list[str]:
    return [name for bit, name in table.items() if value & bit]


def _strings(data: bytes, min_len: int = 5, limit: int = MAX_STRING_ITEMS) -> tuple[list[str], list[str]]:
    asc = [m.group().decode("ascii", "replace") for m in
           re.finditer(rb"[\x20-\x7e]{%d,}" % min_len, data)]
    utf = [m.group().decode("utf-16-le", "replace") for m in
           re.finditer(rb"(?:[\x20-\x7e]\x00){%d,}" % max(4, min_len - 1), data)]
    return asc[:limit], utf[:limit]


def _classify_strings(asc: list[str], utf: list[str]) -> dict:
    alls = asc + utf
    uniq = list(dict.fromkeys(alls))
    buckets: dict[str, list[str]] = {k: [] for k in
                                     ("urls", "ips", "emails", "registry", "paths", "domains", "keywords")}
    notable: list[str] = []

    def add(key, val):
        if len(buckets[key]) < 60 and val not in buckets[key]:
            buckets[key].append(val)

    for s in uniq:
        low = s.lower()
        hit = False
        for m in _URL_RE.finditer(s):
            add("urls", m.group()[:200]); hit = True
        for m in _IP_RE.finditer(s):
            ip = m.group()
            if all(0 <= int(o) <= 255 for o in ip.split(".")) and ip.count(".") == 3:
                if not ip.startswith(("0.", "1.0.0", "127.0.")):
                    add("ips", ip); hit = True
        for m in _EMAIL_RE.finditer(s):
            add("emails", m.group()[:120]); hit = True
        for m in _REG_RE.finditer(s):
            add("registry", m.group()[:200]); hit = True
        for m in _WINPATH_RE.finditer(s):
            add("paths", m.group()[:200]); hit = True
        for m in _DOMAIN_RE.finditer(s):
            add("domains", m.group()[:120]); hit = True
        for kw in _KEYWORDS:
            if kw.lower() in low:
                add("keywords", s[:180]); hit = True
                break
        if not hit and any(h in s for h in _NOTABLE_HINTS) and "\x00" not in s:
            if len(notable) < 80:
                notable.append(s[:180])
    return {
        "total": len(alls),
        "unique": len(uniq),
        "buckets": buckets,
        "notable": notable,
        # keep a small, human-readable sample for the report file
        "sample": uniq[:400],
    }


def _disasm(code: bytes, va: int, bits: int, count: int) -> list[str]:
    if not _CS_AVAILABLE or not code:
        return []
    try:
        mode = capstone.CS_MODE_64 if bits == 64 else capstone.CS_MODE_32
        md = capstone.Cs(capstone.CS_ARCH_X86, mode)
        out = []
        for ins in md.disasm(code, va):
            out.append(f"{ins.address:08x}  {ins.mnemonic:<7} {ins.op_str}".rstrip())
            if len(out) >= count:
                break
        return out
    except Exception:
        return []


def _pe_report(pe, path: Path, data: bytes, cfg: dict) -> dict:
    secs = []
    ep_rva = pe.OPTIONAL_HEADER.AddressOfEntryPoint
    ep_sec = None
    for s in pe.sections:
        name = s.Name.rstrip(b"\x00").decode("ascii", "replace")
        raw = s.SizeOfRawData
        chunk = data[s.PointerToRawData:s.PointerToRawData + min(raw, 4 * 1024 * 1024)]
        chars = int(s.Characteristics)
        secs.append({
            "name": name,
            "va": f"0x{s.VirtualAddress:08x}",
            "vsize": int(s.Misc_VirtualSize),
            "rawsize": int(raw),
            "ptr": int(s.PointerToRawData),
            "entropy": round(util.entropy(chunk), 3),
            "flags": " ".join(_mask(chars, SEC_CHARS)) or "-",
            "exec": bool(chars & 0x20000000),
            "write": bool(chars & 0x80000000),
            "read": bool(chars & 0x40000000),
        })
        if s.VirtualAddress <= ep_rva < s.VirtualAddress + max(int(s.Misc_VirtualSize), int(raw)):
            ep_sec = secs[-1]

    imports: dict[str, list[str]] = {}
    import_count = 0
    ordinal_imports = 0
    if hasattr(pe, "DIRECTORY_ENTRY_IMPORT"):
        for entry in pe.DIRECTORY_ENTRY_IMPORT:
            dll = entry.dll.decode("ascii", "replace")
            names = []
            for imp in entry.imports:
                if imp.name:
                    names.append(imp.name.decode("ascii", "replace"))
                else:
                    ordinal_imports += 1
                    names.append(f"#{imp.ordinal}")
            imports[dll] = names[:400]
            import_count += len(entry.imports)

    exports = []
    if hasattr(pe, "DIRECTORY_ENTRY_EXPORT"):
        for sym in pe.DIRECTORY_ENTRY_EXPORT.symbols:
            exports.append({
                "name": sym.name.decode("ascii", "replace") if sym.name else f"#{sym.ordinal}",
                "ordinal": sym.ordinal,
                "rva": f"0x{sym.address:08x}",
            })
            if len(exports) >= 2000:
                break

    resources, resource_flags = _resources(pe)
    version_info = _version_info(pe)
    manifest = _manifest(pe)
    debug = _debug(pe)
    signature = _signature(pe, path)
    overlay = _overlay(pe, path)

    # ---- entry point disassembly + exported function prologues ----
    disasm = []
    bits = 64 if pe.OPTIONAL_HEADER.Magic == 0x20B else 32
    ep_off = _rva_to_offset(pe, ep_rva)
    if ep_off is not None and 0 <= ep_off < len(data):
        code = data[ep_off:ep_off + 192]
        disasm.append({"label": f"entry point 0x{ep_rva:08x}",
                       "lines": _disasm(code, pe.OPTIONAL_HEADER.ImageBase + ep_rva, bits,
                                        int(cfg["analysis"]["disasm_count"]))})
    for exp in exports[:3]:
        if exp["name"].startswith("#"):
            continue
        off = _rva_to_offset(pe, int(exp["rva"], 16))
        if off is None or not (0 <= off < len(data)):
            continue
        disasm.append({"label": f"export {exp['name']}", "lines": _disasm(
            data[off:off + 128], pe.OPTIONAL_HEADER.ImageBase + int(exp["rva"], 16), bits,
            min(20, int(cfg["analysis"]["disasm_count"])))})

    # ---- strings ----
    scan = data[:MAX_STRINGS_SCAN]
    asc, utf = _strings(scan, 5, int(cfg["analysis"]["max_strings"]) * 4)
    strings = _classify_strings(asc, utf)
    strings["truncated"] = len(data) > MAX_STRINGS_SCAN

    ts = int(pe.FILE_HEADER.TimeDateStamp)
    return {
        "kind": "PE",
        "pe": {
            "machine": MACHINES.get(int(pe.FILE_HEADER.Machine), hex(int(pe.FILE_HEADER.Machine))),
            "bits": 64 if pe.OPTIONAL_HEADER.Magic == 0x20B else 32,
            "subsystem": SUBSYSTEMS.get(int(pe.OPTIONAL_HEADER.Subsystem), str(pe.OPTIONAL_HEADER.Subsystem)),
            "characteristics": _mask(int(pe.FILE_HEADER.Characteristics), FILE_CHARS),
            "dll_characteristics": _mask(int(pe.OPTIONAL_HEADER.DllCharacteristics), DLL_CHARS),
            "timestamp_raw": ts,
            "timestamp": _ts(ts),
            "linker": f"{pe.OPTIONAL_HEADER.MajorLinkerVersion}.{pe.OPTIONAL_HEADER.MinorLinkerVersion}",
            "image_base": f"0x{pe.OPTIONAL_HEADER.ImageBase:x}",
            "entry_point": f"0x{ep_rva:08x}",
            "entry_section": ep_sec["name"] if ep_sec else None,
            "size_of_image": int(pe.OPTIONAL_HEADER.SizeOfImage),
            "checksum": int(pe.OPTIONAL_HEADER.CheckSum),
            "sections": len(pe.sections),
            "is_dll": bool(pe.FILE_HEADER.Characteristics & 0x2000),
            "is_dotnet": _is_dotnet(pe),
            "has_tls": bool(pe.OPTIONAL_HEADER.DATA_DIRECTORY[9].VirtualAddress),
            "section_alignment": int(pe.OPTIONAL_HEADER.SectionAlignment),
            "file_alignment": int(pe.OPTIONAL_HEADER.FileAlignment),
            "imphash": _imphash(pe),
        },
        "sections": secs,
        "imports": imports,
        "import_count": import_count,
        "ordinal_imports": ordinal_imports,
        "exports": exports,
        "resources": resources,
        "version_info": version_info,
        "manifest": manifest,
        "debug": debug,
        "signature": signature,
        "overlay": overlay,
        "disasm": disasm,
        "strings": strings,
        "resource_flags": resource_flags,
        "warnings": [],
    }


def _is_dotnet(pe) -> bool:
    try:
        d = pe.OPTIONAL_HEADER.DATA_DIRECTORY[14]
        return bool(d.VirtualAddress and d.Size)
    except Exception:
        return False


def _imphash(pe) -> str:
    try:
        return pe.get_imphash() or ""
    except Exception:
        return ""


def _mtime_iso(path: Path) -> str:
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).astimezone().isoformat(timespec="seconds")
    except Exception:
        return ""


def _ts(ts: int) -> str:
    if ts == 0:
        return "0 (cleared)"
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(ts)


def _overlay(pe, path: Path) -> dict:
    try:
        start = pe.get_overlay_data_start_offset()
        size = path.stat().st_size
        if start is None:
            return {"present": False, "offset": None, "size": 0}
        return {"present": True, "offset": int(start), "size": int(size - start)}
    except Exception:
        return {"present": False, "offset": None, "size": 0}


def _signature(pe, path: Path) -> dict:
    try:
        d = pe.OPTIONAL_HEADER.DATA_DIRECTORY[4]
        if not d.VirtualAddress or not d.Size:
            return {"present": False}
        with open(path, "rb") as f:
            f.seek(d.VirtualAddress)  # security dir uses a file offset, not an RVA
            head = f.read(8)
        length = int.from_bytes(head[:4], "little")
        rev = int.from_bytes(head[4:6], "little")
        ctype = int.from_bytes(head[6:8], "little")
        return {"present": True, "size": int(d.Size), "cert_length": length,
                "revision": f"0x{rev:04x}", "type": f"0x{ctype:04x}",
                "note": "Authenticode block present; chain/trust is NOT verified by this tool"}
    except Exception as exc:
        return {"present": False, "error": str(exc)}


def _debug(pe) -> list[dict]:
    out = []
    try:
        for entry in pe.DIRECTORY_ENTRY_DEBUG:
            item = {"type": int(entry.struct.Type)}
            pdb = None
            for attr in ("PdbFileName", "PdbFileNameRaw"):
                v = getattr(getattr(entry, "entry", None), attr, None)
                if isinstance(v, bytes):
                    pdb = v.rstrip(b"\x00").decode("utf-8", "replace")
                    break
            if pdb:
                item["pdb"] = pdb
            out.append(item)
    except Exception:
        pass
    return out


def _version_info(pe) -> dict:
    info = {}
    try:
        if not hasattr(pe, "FileInfo"):
            return info
        for fileinfo in pe.FileInfo:
            entries = fileinfo if isinstance(fileinfo, list) else [fileinfo]
            for fi in entries:
                for st in getattr(fi, "StringTable", []) or []:
                    for k, v in st.entries.items():
                        key = k.decode("utf-8", "replace") if isinstance(k, bytes) else str(k)
                        val = v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
                        info[key] = val
    except Exception:
        pass
    return dict(list(info.items())[:40])


def _manifest(pe) -> dict:
    out = {"present": False}
    try:
        for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries:
            tid = entry.id if entry.id is not None else -1
            if tid != 24:  # RT_MANIFEST
                continue
            for e2 in entry.directory.entries:
                for e3 in e2.directory.entries:
                    d = e3.data.struct
                    raw = pe.get_data(d.OffsetToData, d.Size)
                    txt = raw.decode("utf-8", "replace") if raw[:2] != b"\xff\xfe" else raw.decode("utf-16-le", "replace")
                    txt = txt.replace("\x00", "")
                    out["present"] = True
                    m = re.search(r'requestedExecutionLevel[^>]*level\s*=\s*["\']([^"\']+)', txt)
                    if m:
                        out["requested_execution_level"] = m.group(1)
                    out["ui_access"] = bool(re.search(r'uiAccess\s*=\s*["\']true', txt, re.I))
                    out["dpi_aware"] = bool(re.search(r"dpiAware", txt, re.I))
                    out["snippet"] = re.sub(r"\s+", " ", txt)[:600]
                    return out
    except Exception:
        pass
    return out


def _resources(pe) -> tuple[list[dict], dict]:
    out: list[dict] = []
    flags = {"embedded_pe": [], "largest": None}
    try:
        for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries:
            tid = entry.id if entry.id is not None else -1
            type_name = RES_TYPES.get(tid, entry.name.string.decode("utf-8", "replace") if entry.name else str(tid))
            names = []
            sizes = []
            for e2 in getattr(entry, "directory", []).entries:
                for e3 in getattr(e2, "directory", []).entries:
                    d = e3.data.struct
                    label = e2.name.string.decode("utf-8", "replace") if getattr(e2, "name", None) else str(e2.id)
                    names.append(label)
                    sizes.append(int(d.Size))
                    if type_name in ("RCDATA", "BIN", "DATA") and d.Size > 2:
                        try:
                            head = pe.get_data(d.OffsetToData, min(2, d.Size))
                            if head[:2] == b"MZ":
                                flags["embedded_pe"].append({"type": type_name, "name": label, "size": int(d.Size)})
                        except Exception:
                            pass
            if names:
                item = {"type": type_name, "count": len(names), "total_size": sum(sizes),
                        "sizes": sizes[:50]}
                out.append(item)
                if flags["largest"] is None or sum(sizes) > flags["largest"]["total_size"]:
                    flags["largest"] = {"type": type_name, "total_size": sum(sizes)}
    except Exception:
        pass
    out.sort(key=lambda x: x["total_size"], reverse=True)
    return out[:40], flags


def _type_name_from_ext(ext: str) -> str:
    return {".pdf": "PDF", ".docx": "DOCX", ".pptx": "PPTX", ".xlsx": "XLSX"}.get(ext, "text")


def _observations(r: dict, size: int) -> list[dict]:
    obs: list[dict] = []

    def add(sev, title, detail):
        obs.append({"severity": sev, "title": title, "detail": detail})

    pe = r.get("pe") or {}
    secs = r.get("sections") or []
    imports = r.get("imports") or {}

    if r["kind"] != "PE":
        add("info", f"File is {r['kind']}, not a Windows PE",
            "Only hashes, file-type identification and strings were produced. Deep PE parsing skipped.")
        return obs

    # signature
    path_l = str(r["file"].get("path") or "").lower()
    is_inbox = ("\\windows\\system32\\" in path_l or "\\windows\\syswow64\\" in path_l
                or "\\windows\\" in path_l)
    if not r.get("signature", {}).get("present"):
        if is_inbox:
            add("info", "No embedded signature (Windows inbox location)",
                "Files under %SystemRoot% are normally catalog-signed, so an embedded Authenticode "
                "block is not expected. Verify with Get-AuthenticodeSignature if needed.")
        else:
            add("info", "No embedded Authenticode signature",
                "Normal for in-house tools, open-source builds and installers. Signed malware exists too, "
                "so this is context rather than a verdict.")

    if pe.get("is_dotnet"):
        add("info", ".NET assembly", "Contains a CLR header. Managed code; inspect with dnSpy/ILSpy for IL-level detail.")

    # timestamp sanity
    ts = int(pe.get("timestamp_raw") or 0)
    if ts == 0:
        add("note", "Compile timestamp is zero", "Timestamps are frequently cleared or faked by packers/build pipelines.")
    else:
        from datetime import datetime, timezone
        try:
            if datetime.fromtimestamp(ts, tz=timezone.utc) > datetime.now(tz=timezone.utc):
                add("note", "Compile timestamp is in the future",
                    "Could be clock skew, reproducible builds, or a tampered header.")
        except Exception:
            pass

    # sections
    for s in secs:
        if s["exec"] and s["write"]:
            add("attention", f"Section {s['name']} is writable and executable (RWX)",
                "RWX sections are unusual in normal compilers and are common in unpacking stubs/shellcode loaders.")
        if s["entropy"] >= 7.2:
            add("note", f"High entropy in {s['name']} ({s['entropy']})",
                "Suggests compressed or encrypted content in that section (packing, embedded payload, or compressed resources).")
    if pe.get("entry_section") and pe["entry_section"] not in (".text", "CODE", ""):
        add("note", f"Entry point is in section {pe['entry_section']}",
            "Most compilers put the entry point in .text. Other locations can indicate a packer or custom loader.")
    if len(secs) <= 2 and size > 200 * 1024:
        add("note", "Very few sections for the file size", "A common shape for packed or single-section binaries.")

    # imports
    total_funcs = int(r.get("import_count") or 0)
    if r.get("resource_flags", {}).get("embedded_pe"):
        names = ", ".join(f"{x['type']}:{x['name']} ({util.human(x['size'])})" for x in
                          r["resource_flags"]["embedded_pe"][:4])
        add("attention", "Embedded PE file found in resources", names +
            " — resource sections that start with MZ can be droppers or installers.")
    if total_funcs and total_funcs < 12 and size > 100 * 1024:
        add("note", f"Only {total_funcs} imported functions",
            "Small import tables plus high entropy often mean the real imports are resolved at runtime "
            "(packers, loaders, or legitimate delay-loading).")

    found_cats = {}
    for cat, apis in API_CATEGORIES.items():
        hits = sorted({f for dll, fns in imports.items() for f in fns if f in apis})
        if hits:
            found_cats[cat] = hits

    # Only combinations that are genuinely meaningful get "attention"; single
    # ordinary APIs (GetDC, RegSetValueEx, GetForegroundWindow) are ubiquitous
    # in normal GUI software and would otherwise drown the report in noise.
    hook_apis = {"SetWindowsHookExA", "SetWindowsHookExW", "GetAsyncKeyState",
                 "RegisterRawInputDevices", "GetKeyboardState"}
    service_apis = {"CreateServiceA", "CreateServiceW", "NtLoadDriver", "StartServiceA", "StartServiceW"}
    has_net = "network communication" in found_cats
    has_inject = "process injection / memory manipulation" in found_cats
    has_hook = bool(hook_apis & set(found_cats.get("keystroke / input capture", [])))
    has_screen = "screen capture" in found_cats
    has_service = bool(service_apis & set(found_cats.get("autostart / persistence", [])))

    if has_inject:
        add("attention", "Remote-process memory primitives imported",
            ", ".join(found_cats["process injection / memory manipulation"][:12]) +
            " — also used by debuggers, EDR agents and accessibility tools, so confirm the caller.")
    if has_hook:
        add("attention", "Global input-hook / raw-input APIs imported",
            ", ".join(sorted(hook_apis & set(found_cats["keystroke / input capture"]))) +
            " — legitimate for hotkeys and accessibility, but also the core of keyloggers.")
    if has_service:
        add("attention", "Service or driver installation APIs imported",
            ", ".join(sorted(service_apis & set(found_cats["autostart / persistence"]))) +
            " — used by installers and updaters too; check the strings around the call site.")
    if has_screen and (has_net or has_hook):
        add("attention", "Screen capture combined with network or input hooks",
            "GDI capture plus networking/input monitoring is the shape of remote-viewing tools. "
            "Ordinary apps rarely combine them.")
    if has_inject and has_net:
        add("attention", "Injection + network capability combination",
            "The import table shows both remote-memory primitives and networking. "
            "Present in some security products too; verify behaviour before drawing conclusions.")

    if found_cats:
        parts = []
        for cat, hits in found_cats.items():
            parts.append(f"{cat} ({', '.join(hits[:5])}{'…' if len(hits) > 5 else ''})")
        add("info", "API capability families observed", "; ".join(parts) +
            ". These imports are shared by a great deal of legitimate software; they describe "
            "what the binary *can* do, not what it does.")

    if pe.get("has_tls"):
        add("note", "TLS callbacks present", "TLS callbacks can run before the entry point, a technique used by both protectors and malware.")
    ov = r.get("overlay") or {}
    if ov.get("present") and ov.get("size", 0) > max(64 * 1024, size * 0.1):
        add("note", f"Large overlay ({util.human(ov['size'])})",
            "Data appended after the last section can be an installer payload, signature, or hidden content.")
    for d in r.get("debug") or []:
        pdb = d.get("pdb")
        if pdb and ("\\Users\\" in pdb or "/home/" in pdb):
            add("info", "PDB path exposes a build path", pdb)
    if pe.get("is_dll") and not r.get("exports"):
        add("note", "DLL has no exports", "Could be a resource-only DLL, a loader, or exports resolved by ordinal elsewhere.")
    if not obs:
        add("info", "No notable structural indicators", "Headers and imports look ordinary for a compiled Windows binary.")
    return obs


def _dossier(r: dict) -> str:
    """Compact plain-text briefing for the language model."""
    L: list[str] = []
    f = r["file"]
    L.append(f"FILE: {f['name']}  ({f['size_h']})")
    L.append(f"SHA256: {f['sha256']}")
    L.append(f"TYPE: {r['kind']}" + (f" / {f['file_kind']}" if f.get("file_kind") else ""))
    pe = r.get("pe")
    if pe:
        L.append(f"ARCH: {pe['machine']} ({pe['bits']}-bit), subsystem {pe['subsystem']}, "
                 f"{'DLL' if pe['is_dll'] else 'EXE'}{', .NET' if pe['is_dotnet'] else ''}")
        L.append(f"COMPILED: {pe['timestamp']}  linker {pe['linker']}")
        L.append(f"ENTRY: {pe['entry_point']} in {pe['entry_section']}  imagebase {pe['image_base']}")
        L.append(f"FLAGS: {' '.join(pe['dll_characteristics']) or 'none'}")
        L.append(f"IMPHASH: {pe['imphash']}")
        L.append(f"SIGNED: {'yes (chain not verified)' if r['signature'].get('present') else 'no'}")
    L.append("")
    if r.get("sections"):
        L.append("SECTIONS (name, vsize, rawsize, entropy, flags):")
        for s in r["sections"]:
            L.append(f"  {s['name']:<10} {s['vsize']:>9} {s['rawsize']:>9} {s['entropy']:>6}  {s['flags']}")
        L.append("")
    if r.get("imports"):
        L.append(f"IMPORTS: {r['import_count']} functions from {len(r['imports'])} DLLs"
                 + (f", {r['ordinal_imports']} by ordinal" if r.get("ordinal_imports") else ""))
        shown = 0
        for dll, fns in r["imports"].items():
            if shown >= 500:
                L.append("  ... (truncated)")
                break
            L.append(f"  {dll}: {', '.join(fns[:40])}")
            shown += len(fns[:40])
        L.append("")
    if r.get("exports"):
        names = [e["name"] for e in r["exports"][:30]]
        L.append(f"EXPORTS ({len(r['exports'])}): {', '.join(names)}")
        L.append("")
    if r.get("resources"):
        L.append("RESOURCES: " + ", ".join(
            f"{x['type']}x{x['count']} ({util.human(x['total_size'])})" for x in r["resources"][:12]))
        L.append("")
    if r.get("version_info"):
        L.append("VERSION INFO: " + "; ".join(f"{k}={v}" for k, v in list(r["version_info"].items())[:14]))
        L.append("")
    if r.get("manifest", {}).get("present"):
        m = r["manifest"]
        L.append(f"MANIFEST: level={m.get('requested_execution_level', '?')} uiAccess={m.get('ui_access')}")
        L.append("")
    if r.get("debug"):
        for d in r["debug"][:3]:
            if d.get("pdb"):
                L.append(f"DEBUG PDB: {d['pdb']}")
        L.append("")
    ov = r.get("overlay") or {}
    if ov.get("present"):
        L.append(f"OVERLAY: {util.human(ov['size'])} at offset {ov['offset']}")
        L.append("")
    b = r["strings"]["buckets"]
    L.append(f"STRINGS: {r['strings']['total']} extracted, {r['strings']['unique']} unique"
             + (" (scan truncated at 128 MB)" if r["strings"].get("truncated") else ""))
    for key in ("urls", "domains", "ips", "emails", "registry", "paths", "keywords"):
        vals = b.get(key) or []
        if vals:
            L.append(f"  {key}: " + " | ".join(vals[:12]))
    if r["strings"].get("notable"):
        L.append("  notable: " + " | ".join(r["strings"]["notable"][:20]))
    L.append("")
    if r.get("disasm"):
        L.append("DISASSEMBLY (excerpt):")
        for blk in r["disasm"][:2]:
            L.append(f"  [{blk['label']}]")
            for line in blk["lines"][:24]:
                L.append("    " + line)
        L.append("")
    L.append("STRUCTURAL OBSERVATIONS:")
    for o in r.get("observations", []):
        L.append(f"  - [{o['severity']}] {o['title']}: {o['detail']}")
    return "\n".join(L)[:14000]


def analyze(path: str | Path, cfg: dict, save: bool = True) -> dict:
    t0 = time.time()
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    size = path.stat().st_size
    if size == 0:
        raise ValueError("file is empty")

    with open(path, "rb") as f:
        data = f.read(MAX_STRINGS_SCAN)
    head = data[:4096]
    kind = _file_kind(head)
    report: dict = {
        "id": "",
        "file": {
            "name": path.name,
            "path": str(path),
            "size": size,
            "size_h": util.human(size),
            "file_kind": kind,
            "md5": _sha("md5", path),
            "sha1": _sha("sha1", path),
            "sha256": _sha("sha256", path),
            "modified": _mtime_iso(path),
        },
        "kind": "PE" if kind == "Windows PE" else kind,
        "pe": None,
        "sections": [],
        "imports": {},
        "import_count": 0,
        "ordinal_imports": 0,
        "exports": [],
        "resources": [],
        "version_info": {},
        "manifest": {"present": False},
        "debug": [],
        "signature": {"present": False},
        "overlay": {"present": False, "size": 0},
        "disasm": [],
        "strings": {"total": 0, "unique": 0, "buckets": {}, "notable": [], "sample": []},
        "observations": [],
        "warnings": [],
        "analyzed_at": util.now_iso(),
    }

    if kind == "Windows PE" and pefile is not None:
        try:
            pe = pefile.PE(str(path), fast_load=False)
            try:
                report.update(_pe_report(pe, path, data, cfg))
            finally:
                try:
                    pe.close()
                except Exception:
                    pass
        except Exception as exc:
            report["warnings"].append(f"PE parsing failed part-way: {exc.__class__.__name__}: {exc}")
            report["observations"].append({
                "severity": "info", "title": "Malformed or unusual PE",
                "detail": "The parser could not complete. Truncated/corrupted headers are themselves a signal. "
                          "Try `llvm-readobj --file-headers` or a fresh copy of the file."})
    else:
        if kind == "Windows PE" and pefile is None:
            report["warnings"].append("pefile is not available; PE deep parsing skipped.")
        asc, utf = _strings(data, 5, int(cfg["analysis"]["max_strings"]) * 4)
        report["strings"] = _classify_strings(asc, utf)

    report["id"] = report["file"]["sha256"][:16]
    report["observations"] = _observations(report, size)
    report["summary"] = {
        "name": report["file"]["name"],
        "kind": report["kind"],
        "size_h": report["file"]["size_h"],
        "sha256": report["file"]["sha256"],
        "signed": bool(report.get("signature", {}).get("present")),
        "dotnet": bool((report.get("pe") or {}).get("is_dotnet")),
        "machine": (report.get("pe") or {}).get("machine"),
        "imports": report.get("import_count", 0),
        "exports": len(report.get("exports") or []),
        "sections": len(report.get("sections") or []),
        "max_entropy": max([s["entropy"] for s in report.get("sections") or [{"entropy": 0}]], default=0),
        "attention": sum(1 for o in report["observations"] if o["severity"] == "attention"),
    }
    report["dossier"] = _dossier(report)
    report["duration_ms"] = int((time.time() - t0) * 1000)
    report["note"] = "Static, read-only analysis. The file was not executed. Indicators are not a verdict."

    if save:
        paths.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        util.write_json(paths.REPORTS_DIR / f"{report['id']}.json", report)
        idx_file = paths.REPORTS_DIR / "_index.json"
        idx = util.read_json(idx_file, []) or []
        idx = [x for x in idx if x.get("id") != report["id"]]
        idx.insert(0, {"id": report["id"], "name": report["file"]["name"],
                       "sha256": report["file"]["sha256"], "size_h": report["file"]["size_h"],
                       "kind": report["kind"], "analyzed_at": report["analyzed_at"],
                       "attention": report["summary"]["attention"]})
        util.write_json(idx_file, idx[:200])
    return report


def analyze_folder_scan(folder: str, cfg: dict, limit: int = 50) -> list[dict]:
    """Lightweight triage of a folder: hashes/kind/indicators, no full string dump."""
    out = []
    root = Path(folder)
    for p in sorted(root.rglob("*")):
        if len(out) >= limit:
            break
        if p.is_file() and p.suffix.lower() in (".exe", ".dll", ".sys", ".scr", ".ocx", ".cpl"):
            try:
                r = analyze(p, cfg, save=False)
                out.append(r["summary"])
            except Exception as exc:
                out.append({"name": p.name, "error": str(exc)})
    return out
