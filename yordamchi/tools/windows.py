"""Oynalar va UI elementlari: ro'yxat, fokus, yopish, UI Automation daraxti, klik, matn yozish."""

from __future__ import annotations

import ctypes
import re
import time
from dataclasses import dataclass
from typing import Annotated, Literal

import psutil
import win32api
import win32con
import win32gui
import win32process

from . import _input
from ._fuzzy import rank
from .base import Risk, ToolError, registry

dwmapi = ctypes.WinDLL("dwmapi")
DWMWA_CLOAKED = 14
_SKIP_TITLES = {"Program Manager", "Windows Input Experience", "Microsoft Text Input Application"}


@dataclass
class Win:
    hwnd: int
    title: str
    process: str
    pid: int
    minimized: bool

    def brief(self) -> dict:
        d = {"id": self.hwnd, "title": self.title[:90], "process": self.process}
        if self.minimized:
            d["minimized"] = True
        return d


def _cloaked(hwnd: int) -> bool:
    val = ctypes.c_int(0)
    dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val))
    return val.value != 0


def _proc_name(pid: int) -> str:
    try:
        return psutil.Process(pid).name()
    except (psutil.Error, OSError):
        return "?"


def list_top_windows() -> list[Win]:
    res: list[Win] = []

    def cb(h, _):
        try:
            if not win32gui.IsWindowVisible(h):
                return True
            title = win32gui.GetWindowText(h)
            if not title or title in _SKIP_TITLES:
                return True
            if win32gui.GetWindowLong(h, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOOLWINDOW:
                return True
            if _cloaked(h):
                return True
            _, pid = win32process.GetWindowThreadProcessId(h)
            res.append(Win(h, title, _proc_name(pid), pid, bool(win32gui.IsIconic(h))))
        except Exception:  # noqa: BLE001
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return res


_COUNTER = re.compile(r"\(\d+\)|‎|‏")
_PROC_ALIASES = {"winword": "word", "excel": "excel", "powerpnt": "powerpoint", "code": "vs code visual studio code",
                 "msedge": "edge", "applicationframehost": "", "explorer": "explorer provodnik fayllar"}


def _clean_title(t: str) -> str:
    """'(3) 101 - ГРУППА – (247319)' -> '101 - ГРУППА' : o'qilmagan xabar sonlari va yashirin belgilarsiz."""
    t = _COUNTER.sub("", t).replace("–", "-").replace("—", "-")
    return " ".join(t.split()).strip(" -")


def _keys(w: Win) -> list[str]:
    proc = w.process.removesuffix(".exe").lower()
    title = _clean_title(w.title)
    # "Hujjat - Notepad" -> oxirgi qism odatda dastur nomi
    parts = [p.strip() for p in title.split(" - ") if p.strip()]
    return [w.title, title, proc, _PROC_ALIASES.get(proc, ""), *(parts[-1:] or [])]


def find_windows(query: str) -> list[Win]:
    wins = list_top_windows()
    q = query.strip()
    if q.isdigit():
        return [w for w in wins if w.hwnd == int(q)]
    found = [w for _, w in rank(q, wins, _keys, threshold=0.6)]
    if found:
        return found
    # Sarlavha o'zgargan bo'lishi mumkin ("Telegram (247291)" -> "Telegram (247296)") yoki dastur nomi
    # berilgan ("Word 2013"): raqamsiz va so'zma-so'z moslash
    cq = _clean_title(re.sub(r"\b\d{4}\b", "", q))
    if cq and cq != q:
        found = [w for _, w in rank(cq, wins, _keys, threshold=0.6)]
    return found


def resolve_window(query: str | None) -> Win:
    if not query:
        h = win32gui.GetForegroundWindow()
        if not h:
            raise ToolError("No foreground window")
        _, pid = win32process.GetWindowThreadProcessId(h)
        return Win(h, win32gui.GetWindowText(h), _proc_name(pid), pid, bool(win32gui.IsIconic(h)))
    found = find_windows(query)
    if not found:
        raise ToolError(f"No window matches '{query}'. Use list_windows to see open windows.")
    return found[0]


def foreground_brief() -> dict:
    try:
        return resolve_window(None).brief()
    except ToolError:
        return {}


def bring_to_front(hwnd: int) -> bool:
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    fg = win32gui.GetForegroundWindow()
    cur_tid = win32api.GetCurrentThreadId()
    fg_tid = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
    attached = False
    try:
        if fg_tid and fg_tid != cur_tid:
            attached = bool(ctypes.windll.user32.AttachThreadInput(cur_tid, fg_tid, True))
        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        except Exception:  # noqa: BLE001
            _input.tap_alt()
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:  # noqa: BLE001
                pass
    finally:
        if attached:
            ctypes.windll.user32.AttachThreadInput(cur_tid, fg_tid, False)
    for _ in range(10):
        if win32gui.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.05)
    return False


# --------------------------------------------------------------------------- window tools

@registry.tool()
def list_windows() -> dict:
    """List open application windows (id, title, process). The first one marked active is in front."""
    fg = win32gui.GetForegroundWindow()
    wins = list_top_windows()
    return {"active_id": fg, "windows": [w.brief() for w in wins[:40]], "count": len(wins)}


@registry.tool()
def focus_window(query: Annotated[str, "Window title, app name or window id"]) -> dict:
    """Bring a window to the front (restores it if minimized)."""
    w = resolve_window(query)
    ok = bring_to_front(w.hwnd)
    return {"ok": ok, "window": w.brief(), "verified_foreground": ok}


@registry.tool()
def window_action(
    query: Annotated[str, "Window title, app name or window id"],
    action: Literal["minimize", "maximize", "restore"],
) -> dict:
    """Minimize, maximize or restore a window."""
    w = resolve_window(query)
    cmd = {"minimize": win32con.SW_MINIMIZE, "maximize": win32con.SW_MAXIMIZE, "restore": win32con.SW_RESTORE}[action]
    win32gui.ShowWindow(w.hwnd, cmd)
    time.sleep(0.2)
    state = "minimized" if win32gui.IsIconic(w.hwnd) else (
        "maximized" if win32gui.GetWindowPlacement(w.hwnd)[1] == win32con.SW_SHOWMAXIMIZED else "normal")
    return {"window": w.brief(), "state": state}


@registry.tool()
def close_window(
    query: Annotated[str, "Window title, app name or window id"],
    all_matching: Annotated[bool, "Close every matching window, not only the best match"] = False,
) -> dict:
    """Close a whole window gracefully (like clicking X). The app may show a 'save changes?' dialog.
    For a browser this closes ALL its tabs - to close one tab use press_keys('ctrl+w') instead."""
    found = find_windows(query)
    if not found:
        raise ToolError(f"No window matches '{query}'")
    if not all_matching and len(found) > 1:
        same_app = {w.process for w in found}
        if len(same_app) > 1:
            return {"ok": False, "ambiguous": True, "candidates": [w.brief() for w in found[:8]],
                    "hint": "Several windows match. Ask the user which one, or pass the window id."}
    targets = found if all_matching else found[:1]
    for w in targets:
        win32gui.PostMessage(w.hwnd, win32con.WM_CLOSE, 0, 0)
    deadline = time.time() + 4
    while time.time() < deadline and any(win32gui.IsWindow(w.hwnd) for w in targets):
        time.sleep(0.15)
    still = [w.brief() for w in targets if win32gui.IsWindow(w.hwnd) and win32gui.IsWindowVisible(w.hwnd)]
    res = {"closed": [w.brief() for w in targets if w.brief() not in still], "still_open": still}
    if still:
        res["ok"] = False
        res["hint"] = "Window did not close; it may be showing a save dialog. Check with get_ui_elements."
        res["active_window"] = foreground_brief()
    return res


@registry.tool(risk=Risk.CONFIRM,
               confirm=lambda a: f"'{a.get('name')}' jarayonini majburan to'xtatish (saqlanmagan ishlar yo'qoladi)")
def kill_process(name: Annotated[str, "Process name like 'chrome' or 'notepad.exe', or a PID"]) -> dict:
    """Force-kill a process (unsaved work is lost). Prefer close_window first."""
    n = name.lower().removesuffix(".exe")
    procs = [p for p in psutil.process_iter(["pid", "name"])
             if (n.isdigit() and p.info["pid"] == int(n)) or (p.info["name"] or "").lower().removesuffix(".exe") == n]
    if not procs:
        raise ToolError(f"No process named '{name}'")
    for p in procs:
        try:
            p.kill()
        except psutil.Error:
            pass
    gone, alive = psutil.wait_procs(procs, timeout=3)
    return {"killed": len(gone), "still_running": len(alive)}


# --------------------------------------------------------------------------- UI automation

_ELEMENTS: dict[int, object] = {}
_ELEMENTS_WIN: dict = {}  # oxirgi get_ui_elements qaysi oynadan: {"win": Win, "names": {n: name}}
_INTERESTING = {
    "ButtonControl", "EditControl", "MenuItemControl", "ListItemControl", "TabItemControl",
    "HyperlinkControl", "CheckBoxControl", "RadioButtonControl", "ComboBoxControl", "TreeItemControl",
    "DocumentControl", "SplitButtonControl", "DataItemControl", "SliderControl", "SpinnerControl",
}


def _value(c) -> str | None:
    try:
        vp = c.GetValuePattern()
        v = vp.Value if vp else None
        return v[:80] if v else None
    except Exception:  # noqa: BLE001
        return None


@registry.tool(timeout=30)
def get_ui_elements(
    window: Annotated[str | None, "Window title/app/id; empty = active window"] = None,
    filter_text: Annotated[str | None, "Only elements whose name contains this text"] = None,
    max_elements: int = 60,
) -> dict:
    """Read clickable/typeable UI elements of a window (buttons, fields, menu items, list items).
    Returns numbered elements; use the numbers with click_element / set_element_text.
    Numbers are valid only until the UI changes, so call this again after actions."""
    import uiautomation as auto

    w = resolve_window(window)
    if window:
        bring_to_front(w.hwnd)
    root = auto.ControlFromHandle(w.hwnd)
    if root is None:
        raise ToolError("Cannot access UI of this window")
    _ELEMENTS.clear()
    _ELEMENTS_WIN.clear()
    _ELEMENTS_WIN.update(win=w, names={})
    out: list[dict] = []
    visited = 0
    t0 = time.time()
    flt = filter_text.lower() if filter_text else None
    stack = [(root, 0)]
    while stack and len(out) < max_elements and visited < 3000 and time.time() - t0 < 6:
        c, depth = stack.pop()
        visited += 1
        try:
            kids = c.GetChildren() if depth < 25 else []
        except Exception:  # noqa: BLE001
            kids = []
        stack.extend((k, depth + 1) for k in reversed(kids))
        if c is root:
            continue
        try:
            ctype = c.ControlTypeName
            if ctype not in _INTERESTING or c.IsOffscreen or not c.IsEnabled:
                continue
            name = (c.Name or "").strip()
            if not name and ctype not in ("EditControl", "DocumentControl", "ComboBoxControl"):
                continue
            if flt and flt not in name.lower():
                continue
            idx = len(out) + 1
            _ELEMENTS[idx] = c
            _ELEMENTS_WIN["names"][idx] = name
            item = {"n": idx, "type": ctype.removesuffix("Control"), "name": name[:60]}
            if ctype in ("EditControl", "ComboBoxControl"):
                v = _value(c)
                if v:
                    item["value"] = v
            out.append(item)
        except Exception:  # noqa: BLE001
            continue
    return {"window": w.brief(), "elements": out, "truncated": len(out) >= max_elements,
            "elapsed_ms": int((time.time() - t0) * 1000)}


@registry.tool(timeout=30)
def read_window_text(
    window: Annotated[str | None, "Window title/app/id; empty = active window"] = None,
    max_chars: int = 3000,
) -> dict:
    """Read the text a window is showing (labels, results, messages, field contents).
    Use it to check what an app actually displays after an action, e.g. a calculator result."""
    import uiautomation as auto

    w = resolve_window(window)
    root = auto.ControlFromHandle(w.hwnd)
    if root is None:
        raise ToolError("Cannot access UI of this window")
    seen: set[str] = set()
    lines: list[str] = []
    total = 0
    stack = [(root, 0)]
    visited, t0 = 0, time.time()
    while stack and total < max_chars and visited < 4000 and time.time() - t0 < 6:
        c, depth = stack.pop()
        visited += 1
        try:
            kids = c.GetChildren() if depth < 25 else []
        except Exception:  # noqa: BLE001
            kids = []
        stack.extend((k, depth + 1) for k in reversed(kids))
        try:
            if c.IsOffscreen:
                continue
            ctype = c.ControlTypeName
            txt = None
            if ctype in ("TextControl", "HeaderItemControl", "StatusBarControl", "TitleBarControl"):
                txt = c.Name
            elif ctype in ("EditControl", "DocumentControl"):
                txt = _value(c) if ctype == "EditControl" else None
                if ctype == "DocumentControl":
                    try:
                        tp = c.GetTextPattern()
                        txt = tp.DocumentRange.GetText(max_chars) if tp else None
                    except Exception:  # noqa: BLE001
                        txt = None
            if txt:
                txt = " ".join(txt.split())
                if txt and txt not in seen:
                    seen.add(txt)
                    lines.append(txt)
                    total += len(txt)
        except Exception:  # noqa: BLE001
            continue
    text = "\n".join(lines)
    return {"window": w.brief(), "text": text[:max_chars], "truncated": len(text) > max_chars}


def _element(n: int):
    c = _ELEMENTS.get(n)
    if c is None:
        raise ToolError(f"Element {n} unknown. Call get_ui_elements first.")
    return c


@registry.tool(timeout=20)
def click_element(n: Annotated[int, "Element number from get_ui_elements"],
                  double: bool = False,
                  position: Annotated[Literal["center", "left", "right"],
                                      "Where to click inside the element. 'left' hits an icon at its left edge, "
                                      "e.g. the download/open icon of a file in a Telegram message"] = "center",
                  ) -> dict:
    """Click a UI element by its number from get_ui_elements."""
    c = _element(n)
    name = c.Name
    done = False
    if position != "center":
        import uiautomation as auto

        r = c.BoundingRectangle
        if r.width() <= 0 or r.height() <= 0:
            raise ToolError("Element is not visible on screen; scroll to it first")
        pad = min(32, max(8, r.width() // 6))
        x = r.left + pad if position == "left" else r.right - pad
        y = r.top + min(r.height() // 2, 40)
        (auto.DoubleClick if double else auto.Click)(x, y)
        done = True
    if not double and not done:
        for getter in ("GetInvokePattern", "GetSelectionItemPattern", "GetTogglePattern", "GetExpandCollapsePattern"):
            try:
                p = getattr(c, getter)()
            except Exception:  # noqa: BLE001
                p = None
            if not p:
                continue
            try:
                {"GetInvokePattern": lambda: p.Invoke(), "GetSelectionItemPattern": lambda: p.Select(),
                 "GetTogglePattern": lambda: p.Toggle(), "GetExpandCollapsePattern": lambda: p.Expand()}[getter]()
                done = True
                break
            except Exception:  # noqa: BLE001
                continue
    if not done:
        if double:
            c.DoubleClick(simulateMove=False)
        else:
            c.Click(simulateMove=False)
    time.sleep(0.5)
    _ELEMENTS.clear()
    return {"clicked": name, "active_window": foreground_brief(),
            "note": "UI may have changed; call get_ui_elements to see the new state."}


@registry.tool(timeout=20, risk=Risk.CONFIRM, confirm_if=lambda a: _element_sends(a),  # pastda aniqlangan
               confirm=lambda a: _element_confirm(a))
def set_element_text(n: Annotated[int, "Element number (an Edit field) from get_ui_elements"],
                     text: str, press_enter: bool = False) -> dict:
    """Put text into an input field (replaces its content) and optionally press Enter."""
    c = _element(n)
    verified = None
    try:
        vp = c.GetValuePattern()
    except Exception:  # noqa: BLE001
        vp = None
    if vp:
        vp.SetValue(text)
        time.sleep(0.1)
        verified = (vp.Value or "") == text
    if not vp or verified is False:
        c.Click(simulateMove=False)
        _input.hotkey("ctrl+a")
        _input.type_text(text)
        try:
            verified = (c.GetValuePattern().Value or "") == text
        except Exception:  # noqa: BLE001
            verified = None
    if press_enter:
        _input.press(_input.VK["enter"])
        time.sleep(0.4)
    return {"field": c.Name, "verified": verified, "active_window": foreground_brief()}


# --------------------------------------------------------------------------- keyboard

_COMBO = re.compile(r"^(?:(?:ctrl|control|alt|shift|win)\s*\+\s*)+\S{1,10}$|^(?:f\d{1,2}|enter|esc|tab)$",
                    re.IGNORECASE)

# Bu ilovalarda Enter = xabar yuborish. STT guruh/odam nomini xato eshitishi mumkin, shuning uchun tasdiq so'raladi.
MESSENGERS = {"telegram.exe": "Telegram", "whatsapp.exe": "WhatsApp", "whatsapp.root.exe": "WhatsApp",
              "slack.exe": "Slack", "teams.exe": "Teams", "ms-teams.exe": "Teams", "discord.exe": "Discord",
              "viber.exe": "Viber", "signal.exe": "Signal"}
_SEND_KEYS = {"enter", "return", "ctrl+enter"}


def _target(args: dict) -> Win | None:
    try:
        return resolve_window(args.get("window"))
    except ToolError:
        return None


def _messenger(args: dict) -> tuple[str, str] | None:
    w = _target(args)
    if w and w.process.lower() in MESSENGERS:
        return MESSENGERS[w.process.lower()], _clean_title(w.title)
    return None


def _types_and_sends(args: dict) -> bool:
    return bool((args.get("press_enter") or "\n" in (args.get("text") or "")) and _messenger(args))


def _keys_send(args: dict) -> bool:
    return (args.get("combo") or "").strip().lower().replace(" ", "") in _SEND_KEYS and bool(_messenger(args))


_SEARCH_FIELD = ("search", "поиск", "qidir", "izla", "найти", "find")


def _element_sends(args: dict) -> bool:
    """set_element_text + Enter messenger'ning xabar maydonida = xabar yuborish (qidiruv maydoni emas).
    Simulyatsiyada model xabarni shu yo'l bilan tasdiqsiz yuborgan."""
    if not (args.get("press_enter") or "\n" in (args.get("text") or "")):
        return False
    w = _ELEMENTS_WIN.get("win")
    if not w or w.process.lower() not in MESSENGERS:
        return False
    try:
        name = (_ELEMENTS_WIN["names"].get(int(args.get("n")), "") or "").lower()
    except (TypeError, ValueError):
        name = ""
    return not any(k in name for k in _SEARCH_FIELD)


def _element_confirm(args: dict) -> str:
    w = _ELEMENTS_WIN.get("win")
    app = MESSENGERS.get(w.process.lower(), "Messenger") if w else "Messenger"
    title = _clean_title(w.title) if w else "?"
    try:  # oyna sarlavhasi eskirgan bo'lishi mumkin — joriysini olamiz
        title = _clean_title(win32gui.GetWindowText(w.hwnd)) or title
    except Exception:  # noqa: BLE001
        pass
    return f"{app}da '{title}' ga xabar yuborish\n  Matn: {(args.get('text') or '')[:300]}"


def _send_confirm(args: dict) -> str:
    m = _messenger(args) or ("Messenger", "?")
    body = args.get("text") or "(xabar maydonidagi matn)"
    return f"{m[0]}da '{m[1]}' ga xabar yuborish\n  Matn: {body[:300]}"

@registry.tool(risk=Risk.CONFIRM, confirm_if=_types_and_sends, confirm=_send_confirm)
def type_text(text: Annotated[str, "Text to type into the currently focused place"],
              window: Annotated[str | None, "Focus this window first (title/app/id)"] = None,
              press_enter: bool = False) -> dict:
    """Type text with the keyboard into the active window (supports Uzbek letters).
    Not for shortcuts: use press_keys for 'ctrl+n', 'enter', 'f5' etc."""
    if _COMBO.match(text.strip()):
        raise ToolError(f"'{text}' looks like a keyboard shortcut; use press_keys(combo='{text.strip()}') instead")
    if window:
        w = resolve_window(window)
        if not bring_to_front(w.hwnd):
            raise ToolError(f"Could not bring '{w.title}' to front")
        time.sleep(0.2)
    _input.type_text(text)
    note = None
    if press_enter and text.rstrip(" ").endswith(("=", "\n")):
        note = "Enter not pressed: text already ends with '=' / newline (would submit twice)"
    elif press_enter:
        _input.press(_input.VK["enter"])
    time.sleep(0.15)
    res = {"typed_chars": len(text), "active_window": foreground_brief()}
    if note:
        res["note"] = note
    return res


@registry.tool(risk=Risk.CONFIRM, confirm_if=_keys_send, confirm=_send_confirm)
def press_keys(combo: Annotated[str, "Key or combination, e.g. 'enter', 'ctrl+s', 'alt+tab', 'win+d', 'pageup'"],
               window: Annotated[str | None, "Focus this window first"] = None,
               times: int = 1) -> dict:
    """Press a keyboard key or shortcut in the active window."""
    if window:
        w = resolve_window(window)
        bring_to_front(w.hwnd)
        time.sleep(0.2)
    for _ in range(max(1, min(times, 50))):
        _input.hotkey(combo)
        time.sleep(0.05)
    time.sleep(0.3)
    return {"pressed": combo, "times": times, "active_window": foreground_brief()}
