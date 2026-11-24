"""Brauzer vkladkalari (Chrome/Edge/Brave) — UI Automation orqali.

Oyna sarlavhasi faqat faol vkladkani ko'rsatadi; logda "barcha YouTube vkladkalarini yop" buyrug'i
5 marta takrorlangan, chunki agent qolgan vkladkalarni ko'ra olmagan. Bu yerda tab strip'ning har bir
TabItem'i o'qiladi va o'zining "Yopish" tugmasi bilan yopiladi (faollashtirmasdan).
"""

from __future__ import annotations

import re
import time
from typing import Annotated

from . import _fuzzy
from .base import ToolError, registry
from .windows import Win, bring_to_front, list_top_windows

BROWSER_PROCS = ("chrome.exe", "msedge.exe", "brave.exe", "opera.exe", "vivaldi.exe", "yandex.exe", "browser.exe")
_CLOSE_NAMES = ("закрыть", "close", "yopish")
# Xotirani tejash rejimi: 'Вкладка "X" неактивна – освобождено 51 МБ' / 'X - Memory usage ...'
_SLEEPING = re.compile(r'^(?:Вкладка|Tab)\s+"(.+)"\s+(?:неактивна|is inactive|inactive).*$', re.IGNORECASE | re.DOTALL)
_SUFFIX = re.compile(r"\s+[-–]\s+(?:Воспроизведение аудио|Playing audio|Звук выключен|Audio muted|"
                     r"Использование памяти.*|Memory usage.*)$", re.IGNORECASE)


def clean_tab_title(name: str) -> str:
    t = (name or "").replace("\xa0", " ").strip()
    m = _SLEEPING.match(t)
    if m:
        t = m.group(1)
    return _SUFFIX.sub("", t).strip()


def _browser_windows() -> list[Win]:
    return [w for w in list_top_windows() if w.process.lower() in BROWSER_PROCS]


def _tab_items(hwnd: int) -> list:
    import uiautomation as auto

    root = auto.ControlFromHandle(hwnd)
    return [c for c, _ in auto.WalkControl(root, maxDepth=14) if c.ControlTypeName == "TabItemControl"]


def all_tabs() -> list[tuple[Win, object, str]]:
    out = []
    for w in _browser_windows():
        for c in _tab_items(w.hwnd):
            out.append((w, c, clean_tab_title(c.Name)))
    return out


def tab_matches(title: str, query: str) -> bool:
    q = _fuzzy.norm(query)
    if not q:
        return False
    t = _fuzzy.norm(title)
    if q in t:
        return True
    words = [x for x in q.split() if len(x) >= 2]
    return bool(words) and all(x in t for x in words)


def _close(win: Win, tab) -> bool:
    """Vkladkani tanlaydi, haqiqatan faol bo'lganini tekshiradi, keyin ctrl+w.
    (Ko'p vkladkada "Yopish" tugmasi yashirin bo'ladi — 0x0 o'lcham, Invoke hech narsa qilmaydi; sinovda tekshirildi.)"""
    import win32gui

    from . import _input

    try:
        bring_to_front(win.hwnd)
    except Exception:  # noqa: BLE001
        pass
    if win32gui.GetForegroundWindow() != win.hwnd:
        raise ToolError("Could not bring the browser window to the front")  # ctrl+w boshqa oynaga ketmasin
    sp = tab.GetSelectionItemPattern()
    sp.Select()
    for _ in range(10):
        time.sleep(0.05)
        if sp.IsSelected:
            break
    else:
        return False  # boshqa vkladkani yopib qo'ymaslik uchun
    _input.hotkey("ctrl+w")
    time.sleep(0.25)
    return True


@registry.tool(timeout=30)
def list_browser_tabs(filter_text: Annotated[str, "Only tabs whose title contains this (e.g. 'youtube')"] = "") -> dict:
    """List ALL open tabs of the browser windows (Chrome/Edge), not only the active one."""
    tabs = all_tabs()
    shown = [t for t in tabs if not filter_text or tab_matches(t[2], filter_text)]
    wins: dict[int, dict] = {}
    for w, _, title in shown:
        e = wins.setdefault(w.hwnd, {"window_id": w.hwnd, "browser": w.process, "tabs": []})
        e["tabs"].append(title)
    for e in wins.values():
        e["count"] = len(e["tabs"])
        e["tabs"] = [x[:80] for x in e["tabs"][:25]]
    return {"total_tabs": len(tabs), "matching": len(shown) if filter_text else None,
            "windows": list(wins.values())}


@registry.tool(timeout=60)
def close_browser_tabs(match: Annotated[str, "Close every tab whose title contains this text, e.g. 'youtube'"]) -> dict:
    """Close all browser tabs whose title matches (in every Chrome/Edge window). Re-checks afterwards."""
    if len(_fuzzy.norm(match)) < 2:
        raise ToolError("Give a word that the tab titles contain (e.g. 'youtube')")
    closed: list[str] = []
    prev = None
    for _ in range(60):  # har yopishdan keyin tab strip qayta o'qiladi (elementlar eskiradi)
        targets = [t for t in all_tabs() if tab_matches(t[2], match)]
        if not targets or (prev is not None and len(targets) >= prev):  # yopilmadi — aylanib qolmaymiz
            if closed and targets and len(targets) >= prev:
                closed.pop()
            break
        prev = len(targets)
        w, tab, title = targets[-1]
        try:
            if not _close(w, tab):
                break
        except ToolError:
            if not closed:
                raise
            break
        except Exception:  # noqa: BLE001
            break
        closed.append(title)
    left = [t[2] for t in all_tabs() if tab_matches(t[2], match)]
    return {"closed": len(closed), "closed_titles": [c[:60] for c in closed[:10]],
            "still_open_matching": len(left), "still_open_titles": [x[:60] for x in left[:5]],
            "note": "ctrl+shift+t in the browser reopens a closed tab"}


@registry.tool(timeout=30)
def switch_browser_tab(match: Annotated[str, "Text from the tab title"]) -> dict:
    """Bring a browser tab to the front (activates its window and selects the tab)."""
    hits = [t for t in all_tabs() if tab_matches(t[2], match)]
    if not hits:
        raise ToolError(f"No open tab matches '{match}'")
    w, tab, title = hits[0]
    bring_to_front(w.hwnd)
    try:
        tab.GetSelectionItemPattern().Select()
    except Exception:  # noqa: BLE001
        tab.Click(simulateMove=False)
    time.sleep(0.3)
    return {"tab": title, "window_id": w.hwnd, "other_matches": len(hits) - 1}


def _address_bar(hwnd: int):
    import uiautomation as auto

    root = auto.ControlFromHandle(hwnd)
    for c, _ in auto.WalkControl(root, maxDepth=14):
        if c.ControlTypeName == "EditControl":
            return c
    return None


@registry.tool(timeout=20)
def get_browser_url(
    window: Annotated[str, "Browser window id; empty = the most recently used browser window"] = "",
    tab: Annotated[str, "Optional: text from a tab title - switches to that tab first"] = "",
    copy: Annotated[bool, "Also copy the link to the clipboard"] = False,
) -> dict:
    """Get the web address (link) of the current tab in Chrome/Edge (e.g. to send it to someone)."""
    if tab:
        switch_browser_tab(tab)
    wins = _browser_windows()
    if window:
        wins = [w for w in wins if str(w.hwnd) == window.strip()] or wins
    if not wins:
        raise ToolError("No browser window is open")
    w = wins[0]  # EnumWindows z-tartibda: birinchisi eng oxirgi ishlatilgan
    bar = _address_bar(w.hwnd)
    if not bar:
        raise ToolError("Address bar not found")
    url = (bar.GetValuePattern().Value or "").strip()
    if not url:
        raise ToolError("Address bar is empty")
    if not re.match(r"^[a-z][a-z0-9+.-]*:", url, re.IGNORECASE):  # Chrome "https://" ni yashiradi
        url = "https://" + url
    if copy:
        from .system import set_clipboard

        set_clipboard(url)
    return {"url": url, "page_title": w.title, "window_id": w.hwnd, "copied": copy}
