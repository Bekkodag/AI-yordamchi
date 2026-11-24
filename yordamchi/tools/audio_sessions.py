"""Ilovalar bo'yicha ovoz sessiyalari (Windows Core Audio):
- qaysi ilova hozir haqiqatan tovush chiqaryapti (peak meter) — "qo'shiq qo'yildimi?" ni tekshirish uchun;
- ducking: JARVIS bilan gaplashganda boshqa ilovalar ovozini vaqtincha pasaytirish (master volume'ga tegmaydi,
  shuning uchun JARVIS ovozi PC darajasida qoladi).

COM chaqiruvlari CoInitialize qilingan oqimda bajarilishi kerak.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ..config import ROOT

BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "yandex.exe", "browser.exe"}
_STATE = ROOT / "data" / "duck_state.json"


def _sessions():
    from pycaw.pycaw import AudioUtilities

    return AudioUtilities.GetAllSessions()


def _name(s) -> str:
    try:
        return s.Process.name() if s.Process else "system"
    except Exception:  # noqa: BLE001
        return "?"


def _peak(s) -> float:
    from pycaw.pycaw import IAudioMeterInformation

    try:
        return float(s._ctl.QueryInterface(IAudioMeterInformation).GetPeakValue())
    except Exception:  # noqa: BLE001
        return 0.0


def playing_apps(sample_s: float = 1.2, threshold: float = 0.003, exclude_self: bool = True) -> dict[str, float]:
    """{process_name: max_peak} — shu vaqt ichida tovush chiqargan ilovalar."""
    me = os.getpid()
    best: dict[str, float] = {}
    end = time.monotonic() + sample_s
    while True:
        for s in _sessions():
            if exclude_self and s.ProcessId == me:
                continue
            p = _peak(s)
            if p > threshold:
                n = _name(s)
                best[n] = max(best.get(n, 0.0), round(p, 3))
        if time.monotonic() >= end:
            return best
        time.sleep(0.15)


def browser_playing(sample_s: float = 1.2) -> bool:
    return any(n.lower() in BROWSERS for n in playing_apps(sample_s))


class Ducker:
    """Boshqa ilovalar ovozini `level` ga ko'paytiradi va keyin aynan oldingi qiymatga qaytaradi.
    Holat diskka yoziladi: JARVIS kutilmaganda yopilsa, keyingi ishga tushishda tiklanadi."""

    def __init__(self) -> None:
        self.saved: dict[str, float] = {}  # session InstanceIdentifier -> original volume

    @staticmethod
    def _key(s) -> str:
        try:
            return s.InstanceIdentifier or f"pid:{s.ProcessId}"
        except Exception:  # noqa: BLE001
            return f"pid:{s.ProcessId}"

    def duck(self, level: float) -> int:
        me = os.getpid()
        n = 0
        for s in _sessions():
            if s.ProcessId in (0, me):
                continue
            k = self._key(s)
            if k in self.saved:
                continue
            try:
                vol = s.SimpleAudioVolume
                cur = float(vol.GetMasterVolume())
                self.saved[k] = cur
                vol.SetMasterVolume(max(0.0, min(1.0, cur * level)), None)
                n += 1
            except Exception:  # noqa: BLE001
                continue
        self._persist()
        return n

    def restore(self) -> int:
        if not self.saved:
            return 0
        n = 0
        for s in _sessions():
            k = self._key(s)
            if k in self.saved:
                try:
                    s.SimpleAudioVolume.SetMasterVolume(self.saved[k], None)
                    n += 1
                except Exception:  # noqa: BLE001
                    pass
        self.saved.clear()
        self._persist()
        return n

    def _persist(self) -> None:
        try:
            _STATE.parent.mkdir(exist_ok=True)
            _STATE.write_text(json.dumps(self.saved), encoding="utf-8")
        except OSError:
            pass

    def recover_from_crash(self) -> int:
        """Oldingi ishga tushirishda pasaytirilib qolgan ovozlarni tiklaydi."""
        try:
            self.saved = json.loads(_STATE.read_text(encoding="utf-8")) if _STATE.exists() else {}
        except (OSError, json.JSONDecodeError):
            self.saved = {}
        return self.restore()
