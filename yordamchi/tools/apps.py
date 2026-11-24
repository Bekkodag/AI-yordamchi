"""Dasturlarni topish va ishga tushirish (Start menyudagi barcha ilovalar: oddiy va Store/UWP)."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Annotated

from ._fuzzy import rank
from .base import ToolError, registry
from .windows import bring_to_front, find_windows, list_top_windows

CREATE_NO_WINDOW = 0x08000000
_SKIP_WORDS = ("uninstall", "удал", "readme", "release notes", "documentation", "website", "o'chirish")

# Talaffuz/STT variantlari -> Start menyudagi nom
ALIASES = {
    "brauzer": "chrome", "xrom": "chrome", "gugl xrom": "chrome",
    "telegramm": "telegram", "word": "word", "vord": "word", "eksel": "excel",
    "kalkulyator": "calculator", "kalkulator": "calculator", "блокнот": "notepad", "bloknot": "notepad",
    "sozlamalar": "settings", "fayllar": "file explorer", "provodnik": "file explorer",
    "explorer": "file explorer", "terminal": "terminal", "vs code": "visual studio code", "vscode": "visual studio code",
}


WEBSITES = {
    "youtube": "https://www.youtube.com", "gmail": "https://mail.google.com", "google": "https://www.google.com",
    "chatgpt": "https://chatgpt.com", "github": "https://github.com", "kun uz": "https://kun.uz", "kun.uz": "https://kun.uz",
    "daryo": "https://daryo.uz", "instagram": "https://www.instagram.com", "facebook": "https://www.facebook.com",
    "google maps": "https://maps.google.com", "translate": "https://translate.google.com",
}


@dataclass(frozen=True)
class App:
    name: str
    app_id: str


class AppIndex:
    def __init__(self) -> None:
        self._apps: list[App] = []
        self._loaded_at = 0.0
        self._lock = threading.Lock()

    def load(self, force: bool = False) -> list[App]:
        with self._lock:
            if self._apps and not force and time.time() - self._loaded_at < 600:
                return self._apps
            ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                  "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress")
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                               capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
            data = json.loads(r.stdout.decode("utf-8", "replace") or "[]")
            if isinstance(data, dict):
                data = [data]
            apps = [App(d["Name"], d["AppID"]) for d in data if d.get("Name") and d.get("AppID")]
            self._apps = [a for a in apps if not any(w in a.name.lower() for w in _SKIP_WORDS)]
            self._loaded_at = time.time()
            return self._apps

    def search(self, query: str, limit: int = 5) -> list[tuple[float, App]]:
        q = ALIASES.get(query.strip().lower(), query)
        res = rank(q, self.load(), lambda a: [a.name], threshold=0.6)
        if not res:
            res = rank(q, self.load(force=True), lambda a: [a.name], threshold=0.6)
        return res[:limit]


index = AppIndex()


def _wait_new_windows(before: set[int], timeout: float = 10.0) -> list:
    deadline = time.time() + timeout
    while time.time() < deadline:
        new = [w for w in list_top_windows() if w.hwnd not in before]
        if new:
            time.sleep(0.6)  # sarlavha to'liq yuklanishi uchun
            return [w for w in list_top_windows() if w.hwnd not in before]
        time.sleep(0.25)
    return []


def _owned(hwnd: int) -> bool:
    import win32con
    import win32gui

    try:
        return bool(win32gui.GetWindow(hwnd, win32con.GW_OWNER))
    except Exception:  # noqa: BLE001
        return False


def app_popups(process: str, main_hwnd: int | None = None, settle_s: float = 0.0) -> dict:
    """Ilova ochilgach chiqqan to'siqlar: shu jarayonning qo'shimcha oynalari (dialoglar) va sarlavhadagi
    ogohlantirishlar (aktivatsiya xatosi...). Agent ularni o'zi yopishi uchun natijaga qo'shiladi."""
    import win32con
    import win32gui

    if settle_s:
        time.sleep(settle_s)  # popuplar asosiy oynadan keyin chiqadi
    out: dict = {}
    try:
        from .popups import dismiss_known_popups

        closed = dismiss_known_popups(process, main_hwnd)
        if closed:
            out["auto_closed_popups"] = closed  # ma'lum xavfsiz to'siqlar (aktivatsiya xatosi) kod tomonidan yopildi
    except Exception as e:  # noqa: BLE001
        out["auto_close_error"] = f"{type(e).__name__}: {e}"
    wins = [w for w in list_top_windows() if w.process.lower() == process.lower()]
    popups = []
    for w in wins:
        if w.hwnd == main_hwnd:
            continue
        try:
            owned = bool(win32gui.GetWindow(w.hwnd, win32con.GW_OWNER))
            cls = win32gui.GetClassName(w.hwnd)
        except Exception:  # noqa: BLE001
            owned, cls = False, ""
        if owned or cls in ("#32770", "NUIDialog", "bosa_sdm_msword", "bosa_sdm_XL9"):
            popups.append({"id": w.hwnd, "title": w.title[:80]})
    if popups:
        out["popups"] = popups[:4]
        out["popup_hint"] = ("A dialog/notice is blocking the app. Close it first (its Close/Закрыть/X/Later button, "
                             "via get_ui_elements on that window), then continue the user's task. Never click "
                             "buy/activate/sign-in/accept buttons; ask the user if the dialog asks a real question.")
    return out


@registry.tool(timeout=40)
def open_app(
    name: Annotated[str, "Application name, e.g. 'chrome', 'telegram', 'word', 'notepad', 'calculator'"],
    new_instance: Annotated[bool, "Start a new instance even if already open"] = False,
) -> dict:
    """Open (launch) an application. If it is already open, brings its window to the front instead.
    The result tells whether a window actually appeared."""
    matches = index.search(name)
    if not matches:
        site = WEBSITES.get(name.strip().lower().removesuffix(".com"))
        if site:
            import os
            os.startfile(site)  # noqa: S606
            return {"app": name, "opened_website": site, "note": "Not an installed app; opened the website."}
        exe = shutil.which(name) or shutil.which(name + ".exe")
        if not exe:
            raise ToolError(f"No installed application matches '{name}'. Try search_apps.")
        target_name, launch = name, [exe]
    else:
        top_score, app = matches[0]
        ties = {a.name for s, a in matches if s >= top_score - 0.01}
        if len({t.lower() for t in ties}) > 1 and top_score < 1.0:
            return {"ok": False, "ambiguous": True, "candidates": sorted(ties)[:6],
                    "hint": "Several apps match; ask the user which one, then call open_app with the exact name."}
        target_name, launch = app.name, ["explorer.exe", f"shell:AppsFolder\\{app.app_id}"]

    if not new_instance:
        existing = find_windows(target_name) or find_windows(name)
        if existing:
            main = next((w for w in existing if not _owned(w.hwnd)), existing[0])  # dialog emas, asosiy oyna
            ok = bring_to_front(main.hwnd)
            return {"app": target_name, "already_open": True, "focused": ok, "window": main.brief(),
                    **app_popups(main.process, main.hwnd)}

    before = {w.hwnd for w in list_top_windows()}
    subprocess.Popen(launch, creationflags=CREATE_NO_WINDOW)
    new = _wait_new_windows(before, timeout=25.0 if "office" in (launch[-1] or "").lower() else 10.0)
    if not new:
        return {"ok": False, "app": target_name, "launched": True, "window_appeared": False,
                "hint": "Launch command sent but no new window appeared within 10 s (app may be starting slowly, "
                        "run in tray, or failed). Check with list_windows."}
    # Dialog asosiy oyna bilan birga chiqsa z-tartibda birinchi bo'ladi (Excel'da sinovda) — egasiz oynani olamiz
    main = next((w for w in new if not _owned(w.hwnd)), new[0])
    # To'siqlar ketma-ket chiqadi (Excel: avval "standart dastur" dialogi, keyin "Мастер активации") —
    # biror narsa yopilgan bo'lsa yana tekshiramiz, 4 martagacha
    closed: list[str] = []
    extra: dict = {}
    for i in range(4):
        extra = app_popups(main.process, main.hwnd, settle_s=2.0 if i == 0 else 1.5)
        got = extra.pop("auto_closed_popups", [])
        closed += got
        if not got and (i >= 1 or extra.get("popups")):
            break
    if closed:
        extra["auto_closed_popups"] = closed
    return {"app": target_name, "launched": True, "window_appeared": True, "windows": [w.brief() for w in new[:3]],
            **extra}


@registry.tool()
def search_apps(query: Annotated[str, "Part of an application name"]) -> dict:
    """Search installed applications by name (to find the exact name before open_app)."""
    return {"matches": [{"name": a.name, "score": round(s, 2)} for s, a in index.search(query, limit=10)]}


@registry.tool(timeout=30)
def close_popups(window: Annotated[str, "Window id/title/app whose blocking dialogs to close"]) -> dict:
    """Close known harmless popups of an app (activation-failed notices etc.) and list other dialogs of it.
    open_app already does this automatically; use it when such a notice appears later."""
    from .windows import resolve_window

    w = resolve_window(window)
    return {"window": w.brief(), **app_popups(w.process, w.hwnd)} or {"window": w.brief(), "popups": []}
