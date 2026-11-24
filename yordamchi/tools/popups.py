"""Ilova ochilganda chiqadigan ma'lum, xavfsiz to'siqlarni (aktivatsiya xatosi, "nima yangi") kod o'zi yopadi.

Model bunga ishonchli emas edi: simulyatsiyada bir marta yopdi, bir marta e'tiborsiz qoldirdi, bir marta
"Закрыть"ni topib bosmadi. Shuning uchun bu yerda deterministik qoidalar:

* alohida oyna (masalan "Мастер активации Microsoft Office") — WM_CLOSE (dialog uchun "bekor qilish" bilan teng);
* oyna ichidagi panel — aniq belgi matni (masalan "Изменить ключ продукта") topiladi va FAQAT o'sha panelning
  "Закрыть"/"Close" tugmasi bosiladi. Oyna sarlavhasidagi "Закрыть" (TitleBar) hech qachon bosilmaydi —
  u butun ilovani yopadi.

Faqat "yopish" bosiladi: "Активировать", "Купить", "Войти" kabi tugmalarga tegilmaydi.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import win32con
import win32gui

from .windows import list_top_windows

_CLOSE = {"закрыть", "close", "позже", "later", "не сейчас", "not now", "x", "×"}
_NEVER = ("актив", "activ", "купить", "buy", "войти", "sign in", "установ", "install", "принять", "accept")


@dataclass(frozen=True)
class Rule:
    name: str
    processes: tuple[str, ...]
    window_titles: tuple[str, ...] = ()   # alohida dialog oynalari (sarlavhasida shu matn) -> WM_CLOSE
    markers: tuple[str, ...] = ()         # oyna ichidagi panelni taniydigan matnlar
    dialog_texts: tuple[str, ...] = ()    # alohida dialog (#32770) ichidagi matn -> WM_CLOSE ("Нет"/bekor bilan teng)


# Uydagi PC'da sinalgan Office 2013 ilovalari (24.11): Access va Publisher'da alohida "Мастер активации" oynasi,
# Excel'da "standart dastur emas" dialogi chiqdi
OFFICE = ("winword.exe", "excel.exe", "powerpnt.exe", "msaccess.exe", "mspub.exe", "visio.exe", "onenote.exe",
          "outlook.exe")

RULES = (
    Rule("office_activation", OFFICE,
         window_titles=("мастер активации", "activation wizard"),
         markers=("изменить ключ продукта", "change product key", "сбой активации продукта",
                  "product activation failed", "нелицензированный продукт", "unlicensed product")),
    # "Excel не является программой по умолчанию... Выбрать типы файлов?" — yopish = "Нет", sozlama o'zgarmaydi
    Rule("office_default_program", OFFICE,
         dialog_texts=("не является программой по умолчанию", "is not your default program",
                       "isn't your default program", "is not currently your default")),
)


def rules_for(process: str) -> list[Rule]:
    p = process.lower()
    return [r for r in RULES if p in r.processes]


def _is_close_button(c) -> bool:
    if c.ControlTypeName != "ButtonControl":
        return False
    name = (c.Name or "").strip().lower()
    if name not in _CLOSE or any(n in name for n in _NEVER):
        return False
    par = c.GetParentControl()
    return not (par is not None and par.ControlTypeName == "TitleBarControl")  # ilovani yopib qo'ymaslik


def _panel_close_button(marker):
    """Belgi elementidan yuqoriga chiqib, o'sha panel ichidagi yopish tugmasini topadi (TitleBar emas)."""
    import uiautomation as auto

    node = marker
    for _ in range(4):
        node = node.GetParentControl()
        if node is None or node.ControlTypeName in ("WindowControl", "TitleBarControl"):
            return None
        for c, _d in auto.WalkControl(node, maxDepth=3):
            if _is_close_button(c):
                return c
    return None


def _iter_markers(hwnd: int, markers: tuple[str, ...], max_s: float = 4.0):
    import uiautomation as auto

    root = auto.ControlFromHandle(hwnd)
    if root is None:
        return
    t0 = time.time()
    for c, _d in auto.WalkControl(root, maxDepth=14):
        if time.time() - t0 > max_s:
            return
        name = (c.Name or "").strip().lower()
        # oyna sarlavhasi doim "(Сбой активации продукта)" bo'lib turadi — u to'siq emas
        if name and c.ControlTypeName not in ("WindowControl", "TitleBarControl") and any(m in name for m in markers):
            yield c


def _dialog_has(hwnd: int, texts: tuple[str, ...]) -> bool:
    import uiautomation as auto

    root = auto.ControlFromHandle(hwnd)
    for c, _d in auto.WalkControl(root, maxDepth=6):
        name = (c.Name or "").lower()
        if name and any(t in name for t in texts):
            return True
    return False


def _close_panel(hwnd: int, markers: tuple[str, ...]) -> str | None:
    for m in _iter_markers(hwnd, markers):
        btn = _panel_close_button(m)
        if btn is None:
            continue
        try:
            btn.GetInvokePattern().Invoke()
        except Exception:  # noqa: BLE001
            btn.Click(simulateMove=False)
        return (m.Name or "")[:50]
    return None


def dismiss_known_popups(process: str, main_hwnd: int | None = None, retries: int = 5) -> list[str]:
    """Shu jarayonning ma'lum to'siqlarini yopadi. Nima yopilganini qaytaradi (bo'sh — hech narsa yo'q edi).
    Ilova endigina ishga tushayotganda UIA vaqtincha COMError beradi (Word'da sinovda ko'rildi) — qayta uriniladi."""
    done: list[str] = []
    for attempt in range(retries):
        try:
            _dismiss(process, main_hwnd, done)  # oldingi urinishda yopilganlar `done`da qoladi
            return done
        except Exception:  # noqa: BLE001 — COMError: ilova hali band
            if attempt == retries - 1:
                if done:
                    return done
                raise
            time.sleep(2.0)
    return done


def _dismiss(process: str, main_hwnd: int | None, done: list[str]) -> list[str]:
    rules = rules_for(process)
    if not rules:
        return done
    wins = [w for w in list_top_windows() if w.process.lower() == process.lower()]
    for r in rules:
        for w in wins:
            if any(t in w.title.lower() for t in r.window_titles):
                win32gui.PostMessage(w.hwnd, win32con.WM_CLOSE, 0, 0)
                done.append(f"{r.name}: closed window '{w.title[:60]}'")
        for w in wins:
            if r.dialog_texts and win32gui.GetClassName(w.hwnd) == "#32770" and _dialog_has(w.hwnd, r.dialog_texts):
                win32gui.PostMessage(w.hwnd, win32con.WM_CLOSE, 0, 0)
                done.append(f"{r.name}: closed dialog '{w.title[:60]}'")
        if not r.markers:
            continue
        for h in ([main_hwnd] if main_hwnd else [w.hwnd for w in wins]):
            for _ in range(2):  # ba'zan ikkita panel ketma-ket chiqadi
                closed = _close_panel(h, r.markers)
                if closed is None:
                    break
                done.append(f"{r.name}: closed panel '{closed}'")
                time.sleep(0.6)
    return done
