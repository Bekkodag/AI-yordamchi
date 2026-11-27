"""O'rgatish rejimi: foydalanuvchi qadamlarni o'zi ko'rsatadi, JARVIS yozib oladi va keyin takrorlaydi.

    "Asalim, o'rgatish: burp suite'ni ishga tushirish"  -> start_teaching
    ... foydalanuvchi dasturni ochadi, "Next", "Start Burp" ni bosadi ...
    "Asalim, o'rgatish tugadi"                          -> stop_teaching  -> data/skills/<nom>.json
    "Asalim, burp suite'ni och"                         -> run_skill

Yozish: past darajali sichqoncha/klaviatura hook'lari (WH_MOUSE_LL / WH_KEYBOARD_LL). Har bosishda:
UI Automation elementi (nomi, turi, AutomationId) + oynaning nisbiy koordinatasi (Java/Swing kabi UIA
ko'rsatmaydigan ilovalar uchun zaxira). Yangi ilova oynasi paydo bo'lsa — "launch" qadami (exe yo'li).
Parol maydoniga yozilgan matn SAQLANMAYDI: qadam "secret" deb belgilanadi, takrorlashda foydalanuvchi yozadi.
JARVIS'ning o'zi yuborgan (injected) bosishlar va terminal/shell oynalaridagi bosishlar yozilmaydi.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import queue
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from .config import ROOT

SKILLS_DIR = ROOT / "data" / "skills"

# Bu jarayonlardagi bosishlar qadam emas: ilovani ochish usuli (start menyu, ish stoli, taskbar) yoki JARVIS'ning
# o'z oynasi. Ularning o'rniga yangi oyna paydo bo'lganda "launch" qadami yoziladi.
SHELL_PROCS = {"explorer.exe", "searchhost.exe", "searchapp.exe", "startmenuexperiencehost.exe",
               "shellexperiencehost.exe", "windowsterminal.exe", "conhost.exe", "python.exe", "pythonw.exe",
               "openconsole.exe", "textinputhost.exe", "lockapp.exe"}

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_KEYDOWN, WM_SYSKEYDOWN, WM_QUIT = 0x0100, 0x0104, 0x0012
WM_LBUTTONDOWN, WM_RBUTTONDOWN = 0x0201, 0x0204
LLMHF_INJECTED, LLKHF_INJECTED = 0x01, 0x10

_MODS = {0x10: "shift", 0xA0: "shift", 0xA1: "shift", 0x11: "ctrl", 0xA2: "ctrl", 0xA3: "ctrl",
         0x12: "alt", 0xA4: "alt", 0xA5: "alt", 0x5B: "win", 0x5C: "win"}
_SPECIAL = {0x0D: "enter", 0x09: "tab", 0x1B: "esc", 0x08: "backspace", 0x2E: "delete", 0x25: "left",
            0x26: "up", 0x27: "right", 0x28: "down", 0x21: "pageup", 0x22: "pagedown", 0x24: "home", 0x23: "end",
            **{0x70 + i: f"f{i + 1}" for i in range(12)}}


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


_HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, wt.HINSTANCE, wt.DWORD]
_user32.SetWindowsHookExW.restype = wt.HHOOK
_user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
_user32.CallNextHookEx.restype = ctypes.c_ssize_t
_user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
_user32.ToUnicodeEx.argtypes = [wt.UINT, wt.UINT, ctypes.POINTER(ctypes.c_ubyte), wt.LPWSTR, ctypes.c_int, wt.UINT,
                                wt.HKL]
_user32.GetKeyboardLayout.restype = wt.HKL
_kernel32.GetModuleHandleW.restype = wt.HMODULE


def slug(name: str) -> str:
    s = re.sub(r"[^\w]+", "_", name.lower(), flags=re.UNICODE).strip("_")
    return s[:60] or "skill"


# --------------------------------------------------------------------------- step helpers

def describe(step: dict) -> str:
    """Qadamni odam o'qiydigan qilib (o'zbekcha)."""
    t = step.get("type")
    if t == "launch":
        return f"'{step.get('app') or step.get('process')}' dasturini ochish"
    if t == "click":
        where = f" ({step.get('window_title', '')[:40]})" if step.get("window_title") else ""
        what = f"'{step['name']}' {step.get('ctrl', '').removesuffix('Control').lower()}" if step.get("name") else \
            f"nuqta {int(step['rel'][0] * 100)}%/{int(step['rel'][1] * 100)}%"
        return ("ikki marta bosish: " if step.get("double") else "bosish: ") + what + where
    if t == "text":
        return "parolni siz yozasiz" if step.get("secret") else f"yozish: \"{step.get('text', '')[:40]}\""
    if t == "key":
        return f"tugma: {step.get('combo')}"
    return str(step)


def finalize(steps: list[dict]) -> list[dict]:
    """Yozuvni tozalaydi: qo'shni matnlarni birlashtiradi, ikki marta bosishni belgilaydi."""
    out: list[dict] = []
    for s in steps:
        prev = out[-1] if out else None
        if prev and s["type"] == "text" and prev["type"] == "text" and not s.get("secret") and not prev.get("secret"):
            prev["text"] += s["text"]
            continue
        if (prev and s["type"] == "click" and prev["type"] == "click" and s.get("t", 0) - prev.get("t", 0) < 0.45
                and abs(s["x"] - prev["x"]) <= 4 and abs(s["y"] - prev["y"]) <= 4):
            prev["double"] = True
            continue
        out.append(dict(s))
    for s in out:
        s.pop("x", None)
        s.pop("y", None)
    return out


# --------------------------------------------------------------------------- recorder

class Recorder:
    def __init__(self) -> None:
        self.name = ""
        self.steps: list[dict] = []
        self.active = False
        self._q: queue.Queue = queue.Queue()
        self._hook_thread: threading.Thread | None = None
        self._worker: threading.Thread | None = None
        self._tid = 0
        self._mods: set[str] = set()
        self._t0 = 0.0
        self._known_pids: set[int] = set()
        self._launched: set[str] = set()
        self._procs = None  # keep callbacks alive
        self.errors = 0

    # ---------------------------------------------------------------- lifecycle
    def start(self, name: str) -> None:
        if self.active:
            raise RuntimeError(f"Already teaching '{self.name}'")
        from .tools.windows import list_top_windows

        self.name, self.steps, self.active, self._t0 = name, [], True, time.monotonic()
        self._known_pids = {("hwnd", w.hwnd) if w.process.lower() == "applicationframehost.exe" else w.pid
                            for w in list_top_windows()}
        self._launched = set()
        self._mods = set()
        self._q = queue.Queue()
        ready = threading.Event()
        self._hook_thread = threading.Thread(target=self._hook_loop, args=(ready,), daemon=True, name="teach-hooks")
        self._hook_thread.start()
        if not ready.wait(3) or not self._tid:
            self.active = False
            raise RuntimeError("Could not install input hooks")
        self._worker = threading.Thread(target=self._work, daemon=True, name="teach-worker")
        self._worker.start()

    def stop(self) -> list[dict]:
        if not self.active:
            return []
        self.active = False
        if self._tid:
            _user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        if self._hook_thread:
            self._hook_thread.join(2)
        self._q.put(None)
        if self._worker:
            self._worker.join(5)
        return finalize(self.steps)

    # ---------------------------------------------------------------- hooks (tez bo'lishi shart)
    def _hook_loop(self, ready: threading.Event) -> None:
        self._tid = _kernel32.GetCurrentThreadId()

        def mouse(n, w, lp):
            if n == 0 and w in (WM_LBUTTONDOWN, WM_RBUTTONDOWN):
                m = ctypes.cast(lp, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if not m.flags & LLMHF_INJECTED:
                    self._q.put(("click", m.pt.x, m.pt.y, "right" if w == WM_RBUTTONDOWN else "left",
                                 time.monotonic()))
            return _user32.CallNextHookEx(None, n, w, lp)

        def keyb(n, w, lp):
            if n == 0:
                k = ctypes.cast(lp, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if not k.flags & LLKHF_INJECTED:
                    down = w in (WM_KEYDOWN, WM_SYSKEYDOWN)
                    mod = _MODS.get(k.vkCode)
                    if mod:
                        (self._mods.add if down else self._mods.discard)(mod)
                    elif down:
                        self._q.put(("key", k.vkCode, k.scanCode, frozenset(self._mods), time.monotonic()))
            return _user32.CallNextHookEx(None, n, w, lp)

        self._procs = (_HOOKPROC(mouse), _HOOKPROC(keyb))
        hmod = _kernel32.GetModuleHandleW(None)
        hm = _user32.SetWindowsHookExW(WH_MOUSE_LL, self._procs[0], hmod, 0)
        hk = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._procs[1], hmod, 0)
        if not hm or not hk:
            self._tid = 0
            ready.set()
            return
        ready.set()
        msg = wt.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        _user32.UnhookWindowsHookEx(hm)
        _user32.UnhookWindowsHookEx(hk)

    # ---------------------------------------------------------------- worker (UIA, COM)
    def _work(self) -> None:
        import comtypes

        comtypes.CoInitialize()
        last_poll = 0.0
        while True:
            try:
                ev = self._q.get(timeout=0.4)
            except queue.Empty:
                ev = ()
            if ev is None:
                break
            try:
                if time.monotonic() - last_poll > 0.4:
                    last_poll = time.monotonic()
                    self._poll_new_windows()
                if ev and ev[0] == "click":
                    self._on_click(*ev[1:])
                elif ev and ev[0] == "key":
                    self._on_key(*ev[1:])
            except Exception:  # noqa: BLE001 — bitta hodisa xatosi yozuvni to'xtatmasin
                self.errors += 1

    def _rel_t(self, t: float) -> float:
        return round(t - self._t0, 2)

    def _poll_new_windows(self) -> None:
        import psutil

        from .tools.windows import list_top_windows

        for w in list_top_windows():
            proc = w.process.lower()
            uwp = proc == "applicationframehost.exe"  # barcha UWP ilovalar bitta jarayonda — oyna bo'yicha
            key = ("hwnd", w.hwnd) if uwp else w.pid
            if key in self._known_pids:
                continue
            self._known_pids.add(key)
            if proc in SHELL_PROCS or (not uwp and proc in self._launched):
                continue
            self._launched.add(proc)
            try:
                exe = psutil.Process(w.pid).exe()
            except Exception:  # noqa: BLE001
                exe = ""
            self.steps.append({"type": "launch", "process": proc, "exe": exe, "app": Path(exe).stem if exe else proc,
                               "app_name": start_menu_name(exe, w.title, w.hwnd), "window_title": w.title[:120],
                               "t": self._rel_t(time.monotonic())})

    def _on_click(self, x: int, y: int, button: str, t: float) -> None:
        import uiautomation as auto
        import win32gui

        from .tools.windows import _proc_name

        hwnd = win32gui.WindowFromPoint((x, y))
        top = _user32.GetAncestor(hwnd, 2) or hwnd  # GA_ROOT
        import win32process

        _, pid = win32process.GetWindowThreadProcessId(top)
        proc = _proc_name(pid).lower()
        if proc in SHELL_PROCS:
            self._poll_new_windows()  # start menyudan ochildi — launch qadami keyinroq paydo bo'ladi
            return
        title = win32gui.GetWindowText(top)
        left, top_, right, bottom = win32gui.GetWindowRect(top)
        w_, h_ = max(1, right - left), max(1, bottom - top_)
        step = {"type": "click", "button": button, "process": proc, "window_title": title[:120],
                "rel": [round((x - left) / w_, 4), round((y - top_) / h_, 4)], "win_size": [w_, h_],
                "x": x, "y": y, "t": self._rel_t(t)}
        try:
            c = auto.ControlFromPoint(x, y)
            if c is not None and c.NativeWindowHandle != top and (c.Name or "").strip():
                step.update(name=c.Name.strip()[:80], ctrl=c.ControlTypeName, auto_id=(c.AutomationId or "")[:80])
        except Exception:  # noqa: BLE001
            pass
        if pid not in self._known_pids and ("hwnd", top) not in self._known_pids:
            self._poll_new_windows()
        step["exe"] = self._exe(pid)
        self.steps.append(step)

    def _exe(self, pid: int) -> str:
        import psutil

        try:
            return psutil.Process(pid).exe()
        except Exception:  # noqa: BLE001
            return ""

    def _on_key(self, vk: int, scan: int, mods: frozenset, t: float) -> None:
        import win32gui
        import win32process

        combo_mods = [m for m in ("ctrl", "alt", "win") if m in mods]
        if combo_mods:
            name = _SPECIAL.get(vk) or (chr(vk).lower() if 0x30 <= vk <= 0x5A else f"vk{vk:#x}")
            self.steps.append({"type": "key", "combo": "+".join([*combo_mods, *(["shift"] if "shift" in mods else []),
                                                                   name]), "t": self._rel_t(t)})
            return
        if vk in _SPECIAL:
            self.steps.append({"type": "key", "combo": ("shift+" if "shift" in mods else "") + _SPECIAL[vk],
                               "t": self._rel_t(t)})
            return
        state = (ctypes.c_ubyte * 256)()
        if "shift" in mods:
            state[0x10] = 0x80
        if _user32.GetKeyState(0x14) & 1:  # Caps Lock
            state[0x14] = 0x01
        fg = win32gui.GetForegroundWindow()
        tid, _ = win32process.GetWindowThreadProcessId(fg)
        buf = ctypes.create_unicode_buffer(8)
        n = _user32.ToUnicodeEx(vk, scan, state, buf, 8, 0x4, _user32.GetKeyboardLayout(tid))
        if n <= 0:
            return
        ch = buf.value[:n]
        if not ch.isprintable():
            return
        if self.steps and self.steps[-1]["type"] == "text":
            last = self.steps[-1]
            if not last.get("secret"):
                last["text"] += ch
            return
        secret = False
        try:
            import uiautomation as auto

            secret = bool(auto.GetFocusedControl().IsPassword)
        except Exception:  # noqa: BLE001
            pass
        self.steps.append({"type": "text", "text": "" if secret else ch, "secret": secret, "t": self._rel_t(t)})


RECORDER = Recorder()


def window_aumid(hwnd: int) -> str:
    """UWP oynaning AppUserModelID'si (Start menyudagi app_id bilan bir xil)."""
    try:
        from win32com.propsys import propsys, pscon

        st = propsys.SHGetPropertyStoreForWindow(hwnd, propsys.IID_IPropertyStore)
        return str(st.GetValue(pscon.PKEY_AppUserModel_ID).GetValue() or "")
    except Exception:  # noqa: BLE001
        return ""


def start_menu_name(exe: str, title: str, hwnd: int | None = None) -> str:
    """Start menyudagi ilova nomi: UWP ilovalarni (Kalkulyator — ApplicationFrameHost) exe bilan ochib bo'lmaydi."""
    try:
        from .tools.apps import index

        apps = index.load()
        aumid = window_aumid(hwnd) if hwnd else ""
        for a in apps:
            if (exe and a.app_id.lower() == exe.lower()) or (aumid and a.app_id == aumid):
                return a.name
        hits = index.search(title or Path(exe).stem, limit=1)
        if hits and hits[0][0] >= 0.8:
            return hits[0][1].name
    except Exception:  # noqa: BLE001
        pass
    return ""


# --------------------------------------------------------------------------- storage

def save_skill(name: str, steps: list[dict]) -> Path:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    apps = [s.get("app") or s.get("process") for s in steps if s["type"] in ("launch", "click")]
    data = {"name": name, "created": datetime.now().isoformat(timespec="seconds"),
            "app": next((a for a in apps if a), ""), "steps": steps, "uses": 0}
    if placeholders(steps):
        data["params"] = placeholders(steps)
    path = SKILLS_DIR / f"{slug(name)}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def load_skills() -> list[dict]:
    out = []
    for p in sorted(SKILLS_DIR.glob("*.json")) if SKILLS_DIR.exists() else []:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            d["_path"] = str(p)
            out.append(d)
        except (OSError, json.JSONDecodeError):
            continue
    return out


def find_skill(name: str) -> dict | None:
    from .tools._fuzzy import rank

    skills = load_skills()
    hits = rank(name, skills, keys=lambda s: [s["name"]], threshold=0.5)
    return hits[0][1] if hits and hits[0][0] >= 0.5 else None


# --------------------------------------------------------------------------- replay

def _norm_title(t: str) -> str:
    return re.sub(r"\(\d+\)|\d+|‎|‏", "", t or "").strip().lower()


def _wait_window(process: str, title: str, timeout: float):
    from .tools.windows import list_top_windows

    want = _norm_title(title)
    deadline = time.time() + timeout
    best = None
    while time.time() < deadline:
        wins = [w for w in list_top_windows() if w.process.lower() == process]
        exact = [w for w in wins if _norm_title(w.title) == want]
        loose = [w for w in wins if want and (want in _norm_title(w.title) or _norm_title(w.title) in want)]
        best = (exact or loose or [None])[0]
        if best:
            return best
        time.sleep(0.3)
    return None


def _find_element(hwnd: int, step: dict, timeout: float):
    """Nom bo'yicha; tur va AutomationId mos kelganini afzal ko'radi (bir xil nomli boshqa element bo'lsa)."""
    import uiautomation as auto

    deadline = time.time() + timeout
    while time.time() < deadline:
        root = auto.ControlFromHandle(hwnd)
        t0 = time.time()
        best, best_score = None, -1
        for c, _d in auto.WalkControl(root, maxDepth=25):
            if time.time() - t0 > 3:
                break
            if (c.Name or "").strip()[:80] != step["name"]:
                continue
            sc = (2 if step.get("auto_id") and (c.AutomationId or "")[:80] == step["auto_id"] else 0) +                  (1 if c.ControlTypeName == step.get("ctrl") else 0)
            if sc > best_score:
                best, best_score = c, sc
            if sc == 3:
                break
        if best is not None:
            return best
        time.sleep(0.4)
    return None


def _invoke(c, double: bool, button: str) -> str:
    if not double and button == "left":
        for getter, act in (("GetInvokePattern", "Invoke"), ("GetTogglePattern", "Toggle"),
                            ("GetSelectionItemPattern", "Select"), ("GetExpandCollapsePattern", "Expand")):
            try:
                p = getattr(c, getter)()
                if p:
                    getattr(p, act)()
                    return act.lower()
            except Exception:  # noqa: BLE001
                continue
    {"left": c.DoubleClick if double else c.Click, "right": c.RightClick}[button](simulateMove=False)
    return "mouse"


PARAM = re.compile(r"\{([a-z][a-z0-9_]{0,31})\}")  # vosita parametri nomlari bilan bir xil qoida


def placeholders(steps: list[dict]) -> list[str]:
    """Skill matnidagi {kontakt} kabi o'rinlar: har safar boshqa qiymat bilan ishlatish uchun."""
    out: list[str] = []
    for s in steps:
        if s.get("type") == "text" and not s.get("secret"):
            for m in PARAM.finditer(s.get("text", "")):
                if m.group(1) not in out:
                    out.append(m.group(1))
    return out


def fill(steps: list[dict], params: dict | None) -> list[dict]:
    """O'rinbosarlarni qiymat bilan to'ldiradi; yetishmasa — xato (bo'sh matn yozib yubormaslik uchun)."""
    need = placeholders(steps)
    if not need:
        return steps
    params = {k: v for k, v in (params or {}).items() if v is not None and str(v) != ""}
    missing = [n for n in need if n not in params]
    if missing:
        raise ValueError("missing values for: " + ", ".join(missing))
    out = []
    for s in steps:
        if s.get("type") == "text" and not s.get("secret"):
            s = dict(s, text=PARAM.sub(lambda m: str(params.get(m.group(1), m.group(0))), s["text"]))
        out.append(s)
    return out


def run_steps(steps: list[dict], start: int = 0, step_timeout: float = 20.0, params: dict | None = None) -> dict:
    """Qadamlarni bajaradi. Element topilmasa nisbiy koordinata bilan bosadi; maxfiy matnda to'xtaydi."""
    try:
        steps = fill(steps, params)
    except ValueError as e:
        return {"ok": False, "error": str(e), "params": placeholders(steps)}
    import subprocess

    import win32gui

    from .tools import _input
    from .tools.windows import bring_to_front, list_top_windows

    log: list[str] = []
    for i in range(start, len(steps)):
        s = steps[i]
        t = s["type"]
        try:
            if t == "launch":
                if not [w for w in list_top_windows() if w.process.lower() == s["process"]]:
                    if s.get("app_name"):
                        from .tools.apps import open_app

                        open_app(s["app_name"])  # Start menyu orqali (UWP ham)
                    elif s.get("exe") and s["process"] != "applicationframehost.exe":
                        subprocess.Popen([s["exe"]])
                    else:
                        return {"ok": False, "failed_step": i, "error": "unknown program path", "done": log}
                w = _wait_window(s["process"], s.get("window_title", ""), 45) or \
                    _wait_window(s["process"], "", 5)
                if not w:
                    return {"ok": False, "failed_step": i, "error": f"{s['process']} window did not appear", "done": log}
                log.append(f"{i + 1}. opened {s.get('app')}")
            elif t == "click":
                if (s.get("exe") and s["process"] != "applicationframehost.exe"
                        and not [x for x in list_top_windows() if x.process.lower() == s["process"]]):
                    subprocess.Popen([s["exe"]])  # o'rgatishda dastur allaqachon ochiq edi
                    _wait_window(s["process"], "", 45)
                w = _wait_window(s["process"], s.get("window_title", ""), step_timeout) or \
                    _wait_window(s["process"], "", 3)
                if not w:
                    return {"ok": False, "failed_step": i, "error": f"window '{s.get('window_title')}' not found",
                            "done": log}
                try:
                    bring_to_front(w.hwnd)
                except Exception:  # noqa: BLE001
                    pass
                how = None
                if s.get("name"):
                    c = _find_element(w.hwnd, s, min(step_timeout, 8))
                    if c is not None:
                        how = _invoke(c, s.get("double", False), s.get("button", "left"))
                if how is None:  # UIA ko'rsatmaydigan ilova (Java) — oynadagi nisbiy nuqta
                    # Keyingi ekran yuklanganini bilib bo'lmaydi: o'rgatishdagi pauzani takrorlaymiz (0.8-15 s)
                    prev_t = steps[i - 1].get("t") if i > 0 else None
                    if prev_t is not None and s.get("t") is not None:
                        time.sleep(min(15.0, max(0.8, float(s["t"]) - float(prev_t))))
                    try:
                        bring_to_front(w.hwnd)
                    except Exception:  # noqa: BLE001
                        pass
                    left, top, right, bottom = win32gui.GetWindowRect(w.hwnd)
                    x = int(left + s["rel"][0] * (right - left))
                    y = int(top + s["rel"][1] * (bottom - top))
                    if win32gui.GetForegroundWindow() != w.hwnd:  # boshqa oynaga bosib yubormaylik
                        return {"ok": False, "failed_step": i, "error": "target window is not in front "
                                "(screen locked?) - cannot click by position", "done": log}
                    _input.click(x, y, s.get("button", "left"), bool(s.get("double")))
                    how = "position"
                log.append(f"{i + 1}. {describe(s)} [{how}]")
                time.sleep(0.6)
            elif t == "text":
                if s.get("secret"):
                    return {"ok": False, "paused_for_secret": True, "failed_step": i, "resume_from": i + 1,
                            "done": log, "note": "The user must type the password themselves, then call run_skill "
                                                 "again with start_step=resume_from."}
                _input.type_text(s["text"])
                log.append(f"{i + 1}. typed {len(s['text'])} chars")
            elif t == "key":
                _input.hotkey(s["combo"])
                log.append(f"{i + 1}. key {s['combo']}")
                time.sleep(0.4)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "failed_step": i, "error": f"{type(e).__name__}: {e}", "done": log}
    return {"ok": True, "steps_done": len(steps) - start, "done": log}
