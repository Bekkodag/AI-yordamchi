"""Tizim: ovoz, media, holat, ekran qulfi, quvvat, bufer (clipboard)."""

from __future__ import annotations

import ctypes
import subprocess
import time
from typing import Annotated, Literal

import psutil

from . import _input
from .base import Risk, ToolError, registry

CREATE_NO_WINDOW = 0x08000000


# --------------------------------------------------------------------------- audio

def _endpoint():
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    dev = AudioUtilities.GetSpeakers()
    ev = getattr(dev, "EndpointVolume", None)
    if ev is not None:
        return ev
    iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return ctypes.cast(iface, ctypes.POINTER(IAudioEndpointVolume))


def _vol_state() -> dict:
    ev = _endpoint()
    return {"volume_percent": round(ev.GetMasterVolumeLevelScalar() * 100), "muted": bool(ev.GetMute())}


@registry.tool()
def get_volume() -> dict:
    """Current system volume (percent) and mute state."""
    return _vol_state()


def _big_drop(a: dict) -> bool:
    p, c = a.get("percent"), a.get("change")
    try:
        return (p is not None and int(float(p)) < 10) or (c is not None and int(float(c)) <= -50)
    except (TypeError, ValueError):
        return False


def _vol_text(a: dict) -> str:
    return (f"Ovozni {a['percent']} foizga tushirish" if a.get("percent") is not None
            else f"Ovozni {abs(int(float(a.get('change', 0))))} foizga pasaytirish")


@registry.tool(risk=Risk.CONFIRM, confirm_if=_big_drop, confirm=_vol_text)
def set_volume(
    percent: Annotated[int | None, "Absolute volume 0-100"] = None,
    change: Annotated[int | None, "Relative change, e.g. +10 or -20"] = None,
) -> dict:
    """Set system volume to an absolute percent, or change it relatively. Unmutes the speakers."""
    ev = _endpoint()
    cur = round(ev.GetMasterVolumeLevelScalar() * 100)
    if percent is None and change is None:
        raise ToolError("Give percent or change")
    target = percent if percent is not None else cur + int(change or 0)
    target = max(0, min(100, int(target)))
    ev.SetMasterVolumeLevelScalar(target / 100, None)
    if target > 0:
        ev.SetMute(0, None)
    st = _vol_state()
    return {"previous_percent": cur, **st, "verified": abs(st["volume_percent"] - target) <= 1}


@registry.tool()
def mute(on: Annotated[bool, "true = mute, false = unmute"] = True) -> dict:
    """Mute or unmute system sound."""
    _endpoint().SetMute(1 if on else 0, None)
    st = _vol_state()
    return {**st, "verified": st["muted"] == on}


_APPCOMMAND = {"play_pause": 14, "next_track": 11, "prev_track": 12, "stop": 13}


def _app_command(action: str, playing: dict) -> int:
    """Media buyrug'ini ovoz chiqarayotgan ilovalar oynalariga WM_APPCOMMAND bilan yuboradi."""
    import win32gui

    from .windows import list_top_windows

    procs = {p.lower() for p in playing} or {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe"}
    # Bir nechta oynaga yuborilganda toggle ikki marta bosilib qolmasin: aniq PAUSE(47) / PLAY(46) ishlatamiz
    code = (47 if playing else 46) if action == "play_pause" else _APPCOMMAND[action]
    sent = 0
    for w in list_top_windows():
        if w.process.lower() in procs:
            win32gui.SendMessage(w.hwnd, 0x0319, w.hwnd, code << 16)
            sent += 1
            if action in ("next_track", "prev_track"):  # bu buyruqlar idempotent emas — bitta oynaga
                break
    return sent


@registry.tool()
def media_control(action: Literal["play_pause", "next_track", "prev_track", "stop"]) -> dict:
    """Send a media key to the current player (works only if a video/song is already open in a player or
    a YouTube video page). Result shows which apps are actually producing sound afterwards.
    To start a specific song on YouTube use youtube_play instead."""
    from .audio_sessions import playing_apps

    before = playing_apps(0.4)
    via = "media_key"
    try:
        _input.press(_input.VK[action])
    except OSError:
        # SendInput ba'zi muhitlarda rad etiladi (cheklangan jarayon, UAC oynasi); WM_APPCOMMAND baribir ishlaydi
        via = f"app_command:{_app_command(action, before)}"
    time.sleep(0.8)
    after = playing_apps(1.0)
    return {"sent": action, "via": via, "sound_before": sorted(before), "sound_now": sorted(after),
            "end_conversation": bool(after) and not before,  # qo'shiq davom etdi -> tinglashni to'xtatamiz
            "note": "If sound_now is empty, nothing is playing - do not claim it started."}


@registry.tool()
def audio_status() -> dict:
    """Which apps are producing sound right now (to verify that music/video is really playing)."""
    from .audio_sessions import playing_apps

    apps = playing_apps(1.5)
    return {"playing": bool(apps), "apps": apps}


# --------------------------------------------------------------------------- status

@registry.tool()
def system_status() -> dict:
    """CPU, memory, disk C:, battery and uptime."""
    vm = psutil.virtual_memory()
    du = psutil.disk_usage("C:\\")
    out = {
        "cpu_percent": psutil.cpu_percent(interval=0.4),
        "memory_used_percent": vm.percent,
        "memory_total_gb": round(vm.total / 2**30, 1),
        "disk_c_free_gb": round(du.free / 2**30, 1),
        "disk_c_used_percent": du.percent,
        "uptime_hours": round((time.time() - psutil.boot_time()) / 3600, 1),
    }
    b = psutil.sensors_battery()
    if b:
        out["battery_percent"] = round(b.percent)
        out["charging"] = bool(b.power_plugged)
    return out


@registry.tool()
def top_processes(sort_by: Literal["memory", "cpu"] = "memory", limit: int = 8) -> dict:
    """Processes using the most memory or CPU."""
    procs = list(psutil.process_iter(["name", "memory_info"]))
    if sort_by == "cpu":
        for p in procs:
            try:
                p.cpu_percent(None)
            except psutil.Error:
                pass
        time.sleep(0.5)
    rows = []
    for p in procs:
        try:
            rows.append({"name": p.info["name"], "pid": p.pid,
                         "memory_mb": round(p.info["memory_info"].rss / 2**20) if p.info["memory_info"] else 0,
                         "cpu_percent": p.cpu_percent(None) if sort_by == "cpu" else None})
        except psutil.Error:
            continue
    key = (lambda r: r["cpu_percent"] or 0) if sort_by == "cpu" else (lambda r: r["memory_mb"])
    rows.sort(key=key, reverse=True)
    return {"processes": rows[:max(1, min(limit, 20))]}


# --------------------------------------------------------------------------- session / power

@registry.tool()
def lock_screen() -> dict:
    """Lock the computer (Win+L)."""
    if not ctypes.windll.user32.LockWorkStation():
        raise ToolError("LockWorkStation failed")
    return {"locked": True, "end_conversation": True}


_POWER = {
    "shutdown": ["shutdown", "/s", "/t", "5"],
    "restart": ["shutdown", "/r", "/t", "5"],
    "sign_out": ["shutdown", "/l"],
    "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
}
_POWER_UZ = {"shutdown": "kompyuterni o'chirish", "restart": "kompyuterni qayta yuklash",
             "sign_out": "tizimdan chiqish", "sleep": "uyqu rejimiga o'tkazish"}


@registry.tool(risk=Risk.CONFIRM, confirm=lambda a: _POWER_UZ.get(a.get("action"), str(a)))
def power_action(action: Literal["shutdown", "restart", "sleep", "sign_out"]) -> dict:
    """Shut down, restart, sleep or sign out. Always asks the user to confirm."""
    subprocess.Popen(_POWER[action], creationflags=CREATE_NO_WINDOW)
    return {"action": action, "started": True, "end_conversation": True}


@registry.tool()
def set_brightness(percent: Annotated[int, "Screen brightness 0-100 (laptop screens only)"]) -> dict:
    """Set built-in display brightness (works on laptops, not on most external monitors)."""
    p = max(0, min(100, int(percent)))
    ps = f"(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=1;Brightness={p}}}) | Out-Null"
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, text=True, timeout=20, creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0:
        raise ToolError("Brightness control not supported on this display")
    return {"brightness_percent": p}


# --------------------------------------------------------------------------- clipboard

@registry.tool()
def get_clipboard() -> dict:
    """Read text from the clipboard."""
    import win32clipboard as cb

    cb.OpenClipboard()
    try:
        if not cb.IsClipboardFormatAvailable(cb.CF_UNICODETEXT):
            return {"text": None, "note": "Clipboard has no text"}
        t = cb.GetClipboardData(cb.CF_UNICODETEXT)
    finally:
        cb.CloseClipboard()
    return {"text": t[:4000], "length": len(t)}


@registry.tool()
def set_clipboard(text: str) -> dict:
    """Copy text to the clipboard."""
    import win32clipboard as cb

    cb.OpenClipboard()
    try:
        cb.EmptyClipboard()
        cb.SetClipboardText(text, cb.CF_UNICODETEXT)
    finally:
        cb.CloseClipboard()
    return {"copied_chars": len(text)}
