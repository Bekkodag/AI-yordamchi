"""Fayllar va papkalar: qidirish, ko'chirish, nusxalash, savatga o'chirish, ochish."""

from __future__ import annotations

import ctypes
import os
import shutil
import uuid
from ctypes import wintypes
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

from .base import Risk, ToolError, registry

# --------------------------------------------------------------------------- known folders

_KNOWN = {
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "music": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
}
_ALIASES = {
    "download": "downloads", "yuklamalar": "downloads", "yuklab olinganlar": "downloads", "загрузки": "downloads",
    "ish stoli": "desktop", "рабочий стол": "desktop",
    "document": "documents", "hujjatlar": "documents", "документы": "documents", "my documents": "documents",
    "rasmlar": "pictures", "изображения": "pictures", "photos": "pictures",
    "musiqa": "music", "музыка": "music", "videolar": "videos", "видео": "videos",
    "home": "~", "uy": "~",
}


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


def known_folder(key: str) -> Path:
    u = uuid.UUID(_KNOWN[key])
    g = _GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
    p = ctypes.c_wchar_p()
    try:
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) == 0:
            return Path(p.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(p)
    return Path.home() / key.capitalize()


def resolve_path(raw: str) -> Path:
    s = os.path.expandvars(raw.strip().strip('"').strip("'"))
    if not s:
        raise ToolError("Empty path")
    parts = s.replace("\\", "/").split("/")
    head = parts[0].lower().strip()
    key = _ALIASES.get(head, head)
    if key == "~":
        base = Path.home()
        return base.joinpath(*parts[1:])
    if key in _KNOWN:
        return known_folder(key).joinpath(*[p for p in parts[1:] if p])
    p = Path(os.path.expanduser(s))
    return p if p.is_absolute() else Path.home() / p


def _info(p: Path) -> dict:
    st = p.stat()
    d = {"name": p.name, "path": str(p), "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")}
    if p.is_dir():
        d["is_folder"] = True
    else:
        d["size_kb"] = round(st.st_size / 1024, 1)
    return d


def _date(s: str | None, end: bool = False) -> float | None:
    if not s:
        return None
    d = date.fromisoformat(s.strip()[:10])
    t = datetime.combine(d, datetime.max.time() if end else datetime.min.time())
    return t.timestamp()


# --------------------------------------------------------------------------- tools

@registry.tool()
def list_folder(path: Annotated[str, "Folder path or known name: Downloads, Desktop, Documents, Pictures, Music, Videos"],
                limit: int = 40) -> dict:
    """List a folder's contents, newest first."""
    p = resolve_path(path)
    if not p.is_dir():
        raise ToolError(f"Not a folder: {p}")
    items = []
    for e in os.scandir(p):
        try:
            items.append(_info(Path(e.path)))
        except OSError:
            continue
    items.sort(key=lambda x: x["modified"], reverse=True)
    return {"folder": str(p), "total": len(items), "items": items[:max(1, min(limit, 200))]}


@registry.tool(timeout=90)
def find_files(
    folder: Annotated[str, "Where to search: path or Downloads/Desktop/Documents/..."],
    name_contains: Annotated[str | None, "Part of the file name (case-insensitive)"] = None,
    extensions: Annotated[list[str] | None, "e.g. ['pdf'] or ['jpg','png']"] = None,
    modified_after: Annotated[str | None, "ISO date YYYY-MM-DD, inclusive"] = None,
    modified_before: Annotated[str | None, "ISO date YYYY-MM-DD, inclusive"] = None,
    recursive: bool = False,
    include_folders: bool = False,
    limit: int = 100,
) -> dict:
    """Find files by name, extension and modification date. For 'yesterday' set both dates to yesterday."""
    root = resolve_path(folder)
    if not root.is_dir():
        raise ToolError(f"Not a folder: {root}")
    exts = {e.lower().lstrip(".") for e in (extensions or [])}
    after, before = _date(modified_after), _date(modified_before, end=True)
    needle = name_contains.lower() if name_contains else None
    found, scanned = [], 0
    walker = os.walk(root) if recursive else [(str(root), [], [])]
    for dirpath, dirnames, _ in walker:
        try:
            entries = list(os.scandir(dirpath))
        except OSError:
            continue
        for e in entries:
            scanned += 1
            if scanned > 50000:
                break
            try:
                is_dir = e.is_dir()
                if is_dir and not include_folders:
                    continue
                if exts and (is_dir or Path(e.name).suffix.lower().lstrip(".") not in exts):
                    continue
                if needle and needle not in e.name.lower():
                    continue
                mt = e.stat().st_mtime
                if (after and mt < after) or (before and mt > before):
                    continue
                found.append(Path(e.path))
            except OSError:
                continue
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "$")) and d != "node_modules"]
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    lim = max(1, min(limit, 300))
    return {"folder": str(root), "total": len(found), "files": [_info(p) for p in found[:lim]],
            "truncated": len(found) > lim}


def _unique(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem, suf, i = dest.stem, dest.suffix, 1
    while True:
        cand = dest.with_name(f"{stem} ({i}){suf}")
        if not cand.exists():
            return cand
        i += 1


def _transfer(paths: list[str], destination: str, op) -> dict:
    dest = resolve_path(destination)
    dest.mkdir(parents=True, exist_ok=True)
    done, errors = [], []
    for raw in paths:
        src = resolve_path(raw)
        if not src.exists():
            errors.append({"path": str(src), "error": "not found"})
            continue
        target = _unique(dest / src.name)
        try:
            op(str(src), str(target))
            done.append(str(target))
        except OSError as e:
            errors.append({"path": str(src), "error": str(e)})
    verified = all(Path(p).exists() for p in done)
    return {"ok": not errors, "destination": str(dest), "count": len(done), "results": done[:50],
            "errors": errors[:20], "verified": verified}


@registry.tool(risk=Risk.CONFIRM, confirm_if=lambda a: len(a.get("paths") or []) > 25,
               confirm=lambda a: f"{len(a.get('paths') or [])} ta faylni '{a.get('destination')}' ga ko'chirish",
               timeout=300)
def move_files(paths: Annotated[list[str], "Full paths of files/folders to move"],
               destination: Annotated[str, "Destination folder (created if missing)"]) -> dict:
    """Move files or folders into a folder. Name clashes get ' (1)' suffix."""
    return _transfer(paths, destination, shutil.move)


@registry.tool(timeout=300)
def copy_files(paths: Annotated[list[str], "Full paths of files/folders to copy"],
               destination: Annotated[str, "Destination folder (created if missing)"]) -> dict:
    """Copy files or folders into a folder."""
    def op(s, d):
        (shutil.copytree if Path(s).is_dir() else shutil.copy2)(s, d)
    return _transfer(paths, destination, op)


@registry.tool()
def create_folder(path: str) -> dict:
    """Create a folder (and parents)."""
    p = resolve_path(path)
    p.mkdir(parents=True, exist_ok=True)
    return {"folder": str(p), "verified": p.is_dir()}


@registry.tool()
def rename_path(path: str, new_name: Annotated[str, "New file/folder name only, not a path"]) -> dict:
    """Rename a file or folder (stays in the same folder)."""
    src = resolve_path(path)
    if not src.exists():
        raise ToolError(f"Not found: {src}")
    if any(c in new_name for c in '\\/:*?"<>|'):
        raise ToolError("new_name must be a plain name")
    dst = src.with_name(new_name)
    if dst.exists():
        raise ToolError(f"Already exists: {dst}")
    src.rename(dst)
    return {"renamed_to": str(dst), "verified": dst.exists()}


def _del_text(a: dict) -> str:
    ps = a.get("paths") or []
    names = ", ".join(Path(p).name for p in ps[:5]) + (f" va yana {len(ps) - 5} ta" if len(ps) > 5 else "")
    return f"{len(ps)} ta fayl/papkani savatga (Recycle Bin) o'chirish: {names}"


@registry.tool(risk=Risk.CONFIRM, confirm=_del_text, timeout=120)
def delete_files(paths: Annotated[list[str], "Full paths of files/folders to delete"]) -> dict:
    """Delete files/folders to the Recycle Bin (recoverable). Always asks the user to confirm."""
    from send2trash import send2trash

    done, errors = [], []
    for raw in paths:
        p = resolve_path(raw)
        if not p.exists():
            errors.append({"path": str(p), "error": "not found"})
            continue
        try:
            send2trash(str(p))
            done.append(str(p))
        except OSError as e:
            errors.append({"path": str(p), "error": str(e)})
    return {"ok": not errors, "deleted_to_recycle_bin": len(done), "errors": errors,
            "verified": all(not Path(p).exists() for p in done)}


@registry.tool()
def open_path(path: Annotated[str, "File or folder to open with its default app (folders open in Explorer)"]) -> dict:
    """Open a file with its default program, or a folder in File Explorer."""
    p = resolve_path(path)
    if not p.exists():
        raise ToolError(f"Not found: {p}")
    os.startfile(str(p))  # noqa: S606
    return {"opened": str(p)}


@registry.tool()
def read_text_file(path: str, max_chars: int = 4000) -> dict:
    """Read a text file (txt, md, csv, json, code). Not for binary files like PDF/DOCX."""
    p = resolve_path(path)
    if not p.is_file():
        raise ToolError(f"Not a file: {p}")
    if p.stat().st_size > 20 * 2**20:
        raise ToolError("File too large")
    raw = p.read_bytes()
    if b"\x00" in raw[:4096]:
        raise ToolError("Binary file; cannot read as text")
    for enc in ("utf-8-sig", "cp1251"):
        try:
            txt = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        txt = raw.decode("utf-8", "replace")
    lim = max(200, min(max_chars, 20000))
    return {"path": str(p), "content": txt[:lim], "truncated": len(txt) > lim, "total_chars": len(txt)}
