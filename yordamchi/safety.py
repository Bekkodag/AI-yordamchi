"""Kill switch: global hotkey (default Ctrl+Alt+X) joriy vazifani darhol to'xtatadi."""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Callable

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

user32 = ctypes.WinDLL("user32", use_last_error=True)


class GlobalHotkey(threading.Thread):
    def __init__(self, callback: Callable[[], None], modifiers: int = MOD_CONTROL | MOD_ALT,
                 vk: int = ord("X")) -> None:
        super().__init__(daemon=True, name="kill-switch")
        self.callback, self.modifiers, self.vk = callback, modifiers, vk
        self.registered = threading.Event()
        self.error: str | None = None
        self._tid = 0

    def run(self) -> None:
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, 1, self.modifiers | MOD_NOREPEAT, self.vk):
            self.error = f"RegisterHotKey failed ({ctypes.get_last_error()}); hotkey may be taken"
            self.registered.set()
            return
        self.registered.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    try:
                        self.callback()
                    except Exception:  # noqa: BLE001
                        pass
        finally:
            user32.UnregisterHotKey(None, 1)

    def stop(self) -> None:
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
