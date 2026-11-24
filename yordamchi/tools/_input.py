"""Klaviatura kiritish: SendInput orqali Unicode matn (o'zbekcha belgilar bilan) va tugma kombinatsiyalari."""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
ULONG_PTR = wintypes.WPARAM


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _U(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


VK = {
    "ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "windows": 0x5B,
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "backspace": 0x08,
    "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "space": 0x20, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "page_up": 0x21, "page_down": 0x22, "pgup": 0x21, "pgdn": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "arrowup": 0x26, "arrowdown": 0x28, "arrowleft": 0x25, "arrowright": 0x27,
    "printscreen": 0x2C, "capslock": 0x14,
    "play_pause": 0xB3, "next_track": 0xB0, "prev_track": 0xB1, "stop": 0xB2,
    "volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
}
VK.update({f"f{i}": 0x6F + i for i in range(1, 25)})
_EXTENDED = {0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x26, 0x28, 0x25, 0x27, 0x5B}


def _key(vk: int, up: bool) -> INPUT:
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED else 0)
    return INPUT(type=INPUT_KEYBOARD, u=_U(ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)))


def _uni(code: int, up: bool) -> INPUT:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    return INPUT(type=INPUT_KEYBOARD, u=_U(ki=KEYBDINPUT(wVk=0, wScan=code, dwFlags=flags, time=0, dwExtraInfo=0)))


def _send(inputs: list[INPUT]) -> None:
    if not inputs:
        return
    arr = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        raise OSError(f"SendInput failed ({ctypes.get_last_error()}); target may be an elevated window")


def vk_for(name: str) -> int:
    n = name.strip().lower().replace(" ", "")
    if n in VK:
        return VK[n]
    if len(n) == 1 and n.isascii() and n.isalnum():
        return ord(n.upper())
    if len(n) == 1:  # ` - = [ ] ; ' , . / \ — joriy klaviatura joylashuvi bo'yicha
        r = user32.VkKeyScanW(ord(n))
        if r != -1 and (r & 0xFF) != 0xFF:
            return r & 0xFF
    aliases = {"backtick": "`", "grave": "`", "minus": "-", "plus": "=", "comma": ",", "period": ".",
               "slash": "/", "backslash": "\\", "semicolon": ";"}
    if n in aliases:
        return vk_for(aliases[n])
    raise ValueError(f"Unknown key: {name}")


def press(vk: int) -> None:
    _send([_key(vk, False), _key(vk, True)])


def hotkey(combo: str) -> None:
    """'ctrl+shift+s', 'alt+f4', 'enter', 'win+d'."""
    keys = [vk_for(k) for k in combo.replace(" ", "").split("+") if k]
    if not keys:
        raise ValueError("Empty key combination")
    downs = [_key(k, False) for k in keys]
    ups = [_key(k, True) for k in reversed(keys)]
    _send(downs + ups)


def type_text(text: str, delay: float = 0.0) -> None:
    for ch in text:
        if ch == "\n":
            press(VK["enter"])
            continue
        if ch == "\t":
            press(VK["tab"])
            continue
        units = ch.encode("utf-16-le")
        codes = [int.from_bytes(units[i:i + 2], "little") for i in range(0, len(units), 2)]
        _send([_uni(c, False) for c in codes] + [_uni(c, True) for c in codes])
        if delay:
            time.sleep(delay)


def tap_alt() -> None:
    """SetForegroundWindow cheklovini chetlab o'tish uchun klassik usul."""
    _send([_key(VK["alt"], False), _key(VK["alt"], True)])


_MOUSE_FLAGS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010)}  # DOWN, UP


def click(x: int, y: int, button: str = "left", double: bool = False) -> None:
    """Ekrandagi nuqtani bosadi. SendInput rad etsa (qulflangan ekran, yuqori huquqli oyna) OSError —
    jimgina "bajarildi" deb qolmasligi uchun (uiautomation.Click xatoni ko'rsatmaydi)."""
    if not user32.SetCursorPos(int(x), int(y)):
        raise OSError(f"SetCursorPos failed ({ctypes.get_last_error()})")
    down, up = _MOUSE_FLAGS[button]
    seq = [down, up] * (2 if double else 1)
    _send([INPUT(type=0, u=_U(mi=MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=f, time=0, dwExtraInfo=0)))
           for f in seq])
