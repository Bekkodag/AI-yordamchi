"""Plugin tizimi: yangi funksiyalar yadro kodiga tegmasdan qo'shiladi.

Plugin — papka, ichida `plugin.py`:

    MANIFEST = {
        "id": "phone_alerts",                 # noyob, lotin harflari/raqam/_
        "name": "Telefon qo'ng'irog'i",
        "version": "0.1.0",
        "description": "Kritik vaziyatda qo'ng'iroq qiladi",
        "settings": [                          # Web UI'da avtomatik forma bo'ladi
            {"key": "api_key", "label": "Provayder kaliti", "type": "secret"},
            {"key": "phone", "label": "Telefon raqami", "type": "str"},
        ],
    }

    def setup(ctx):                            # ctx: PluginContext
        @ctx.tool(risk="confirm")
        def call_me(message: str) -> dict:
            "Call the owner's phone and read the message."
            key = ctx.setting("api_key")
            ...
        ctx.on("reply", lambda ev: ...)        # voqealarga obuna
        ctx.every(300, check_feedback)          # har 5 daqiqada
        router = ctx.router()                   # o'z API'si: /api/plugins/<id>/...

Plugin qayerdan yuklanadi:
    * yordamchi/plugins/builtin/<id>/plugin.py  — dastur bilan keladigan
    * <loyiha>/plugins/<id>/plugin.py           — foydalanuvchi va jamiyat plugin'lari

Izolyatsiya: har bir plugin alohida try/except ichida yuklanadi va ishlaydi. Plugin xatosi (import,
setup, tool, voqea ishlovchisi, jadval) yadroni to'xtatmaydi — panelda "xato" holati bilan ko'rinadi.
Har plugin'ning o'z ma'lumot papkasi bor: data/plugins/<id>/.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import re
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..config import ROOT
from ..settings import STORE, Setting
from ..tools.base import Risk, registry

log = logging.getLogger("yordamchi.plugins")

BUILTIN_DIR = Path(__file__).resolve().parent / "builtin"
USER_DIR = ROOT / "plugins"
DATA_DIR = ROOT / "data" / "plugins"
_ID = re.compile(r"^[a-z][a-z0-9_]{1,40}$")

# Yadro chiqaradigan voqealar (plugin'lar obuna bo'ladi)
EVENTS = ("startup", "shutdown", "user_text", "tool_finished", "reply", "error", "voice")


# --------------------------------------------------------------------------- event bus

class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[tuple[str, Callable]]] = {}
        self.errors: list[dict] = []

    def on(self, event: str, fn: Callable, owner: str = "core") -> None:
        if event not in EVENTS:
            raise ValueError(f"Unknown event '{event}'. Known: {EVENTS}")
        self._subs.setdefault(event, []).append((owner, fn))

    def off_owner(self, owner: str) -> None:
        for ev in self._subs:
            self._subs[ev] = [(o, f) for o, f in self._subs[ev] if o != owner]

    def emit(self, event: str, **data: Any) -> None:
        """Hech qachon xato tashlamaydi: obunachining xatosi yoziladi va keyingisi chaqiriladi."""
        for owner, fn in list(self._subs.get(event, ())):
            try:
                fn(dict(data, event=event))
            except Exception as e:  # noqa: BLE001
                self.errors.append({"owner": owner, "event": event, "error": f"{type(e).__name__}: {e}",
                                    "ts": time.time()})
                del self.errors[:-50]
                _record_error(owner, f"event {event}: {type(e).__name__}: {e}")


BUS = EventBus()


# --------------------------------------------------------------------------- context

@dataclass
class PluginInfo:
    id: str
    name: str
    version: str
    description: str
    path: str
    builtin: bool
    enabled: bool = True
    status: str = "not_loaded"      # loaded | disabled | error | not_loaded
    error: str = ""
    tools: list[str] = field(default_factory=list)
    settings: list[str] = field(default_factory=list)
    has_router: bool = False


class PluginContext:
    """Plugin ko'radigan yagona API. Yadro ichki tuzilmasi o'zgarsa ham shu interfeys barqaror qoladi."""

    def __init__(self, info: PluginInfo) -> None:
        self.info = info
        self.id = info.id
        self.data_dir = DATA_DIR / info.id
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log = logging.getLogger(f"yordamchi.plugin.{info.id}")
        self._router = None
        self._timers: list[threading.Event] = []
        self.runtime = None  # Runtime (agent, log) — ishga tushgach beriladi; panel-only rejimda None

    # ---------------------------------------------------------------- settings
    def setting(self, key: str, default: Any = None) -> Any:
        return STORE.get(f"plugin.{self.id}.{key}", default)

    def set_setting(self, key: str, value: Any) -> None:
        STORE.set(f"plugin.{self.id}.{key}", value)

    # ---------------------------------------------------------------- tools
    def tool(self, *, risk: str | Risk = "safe", name: str | None = None, timeout: float = 60.0,
             confirm: Callable[[dict], str] | None = None, confirm_if: Callable[[dict], bool] | None = None):
        r = Risk(risk) if isinstance(risk, str) else risk
        source = f"plugin:{self.id}"

        def deco(fn: Callable) -> Callable:
            from ..tools.base import build_parameters

            safe_fn = _guarded(self.id, fn)
            # sxema asl funksiyadan (plugin modulining global'lari bilan) — string annotatsiyalar ham ishlaydi
            registry.tool(risk=r, name=name or fn.__name__, timeout=timeout, confirm=confirm,
                          confirm_if=confirm_if, source=source, description=inspect.getdoc(fn) or fn.__name__,
                          parameters=build_parameters(fn))(safe_fn)
            self.info.tools.append(name or fn.__name__)
            return fn
        return deco

    # ---------------------------------------------------------------- events & jobs
    def on(self, event: str, fn: Callable[[dict], None]) -> None:
        BUS.on(event, fn, owner=self.id)

    def every(self, seconds: float, fn: Callable[[], None], first_after: float | None = None) -> None:
        """Fon jadvali. Plugin o'chirilganda yoki dastur yopilganda to'xtaydi."""
        stop = threading.Event()
        self._timers.append(stop)

        def loop() -> None:
            if stop.wait(first_after if first_after is not None else seconds):
                return
            while not stop.is_set():
                try:
                    fn()
                except Exception as e:  # noqa: BLE001
                    _record_error(self.id, f"job {fn.__name__}: {type(e).__name__}: {e}")
                if stop.wait(seconds):
                    return

        threading.Thread(target=loop, daemon=True, name=f"plugin-{self.id}-job").start()

    def router(self):
        """Plugin'ning o'z HTTP yo'llari: /api/plugins/<id>/... (panel token bilan himoyalangan)."""
        if self._router is None:
            from fastapi import APIRouter

            self._router = APIRouter()
            self.info.has_router = True
        return self._router

    # ---------------------------------------------------------------- helpers
    def notify(self, text: str, level: str = "info") -> None:
        """Foydalanuvchiga xabar (panelda ko'rinadi; ovozli rejimda — kelajakda aytiladi)."""
        BUS.emit("voice", ev="plugin_notify", plugin=self.id, text=text, level=level)
        if self.runtime is not None:
            try:
                self.runtime.log.write("plugin", plugin=self.id, text=text, level=level)
            except Exception:  # noqa: BLE001
                pass

    def stop(self) -> None:
        for t in self._timers:
            t.set()


def _guarded(pid: str, fn: Callable) -> Callable:
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:
            _record_error(pid, f"tool {fn.__name__}: {type(e).__name__}: {e}")
            raise
    return wrapper


# --------------------------------------------------------------------------- loader

PLUGINS: dict[str, PluginInfo] = {}
CONTEXTS: dict[str, PluginContext] = {}
_LOADED = False


def _record_error(pid: str, msg: str) -> None:
    info = PLUGINS.get(pid)
    if info is not None:
        info.error = msg[-500:]
    log.warning("plugin %s: %s", pid, msg)


def _enabled_map() -> dict:
    from ..settings import STORE as S

    cfg = S._config()
    return dict(cfg.get("plugins.enabled", {}))


def set_enabled(pid: str, enabled: bool) -> None:
    from ..settings import STORE as S

    with S._lock:
        cfg = S._config()
        m = dict(cfg.get("plugins.enabled", {}))
        m[pid] = bool(enabled)
        cfg["plugins.enabled"] = m
        S._save()
    if pid in PLUGINS:
        PLUGINS[pid].enabled = bool(enabled)


def discover() -> list[tuple[Path, bool]]:
    found = []
    for base, builtin in ((BUILTIN_DIR, True), (USER_DIR, False)):
        if not base.exists():
            continue
        for d in sorted(base.iterdir()):
            if d.is_dir() and (d / "plugin.py").exists() and not d.name.startswith(("_", ".")):
                found.append((d, builtin))
    return found


def _load_module(pid: str, path: Path):
    name = f"yordamchi_plugin_{pid}"
    spec = importlib.util.spec_from_file_location(name, path / "plugin.py",
                                                  submodule_search_locations=[str(path)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _register_settings(info: PluginInfo, items: list[dict]) -> None:
    for it in items or []:
        key = f"plugin.{info.id}.{it['key']}"
        STORE.register(Setting(
            key=key, label=it.get("label", it["key"]), type=it.get("type", "str"), default=it.get("default"),
            env=tuple(it.get("env", ())), help=it.get("help", ""), group=f"plugin:{info.id}",
            section=info.name, choices=tuple(it.get("choices", ())), restart=it.get("restart", False),
            required=it.get("required", False)))
        info.settings.append(key)


def load_all_plugins(force: bool = False) -> dict[str, PluginInfo]:
    """Barcha plugin'larni topadi va yoqilganlarini yuklaydi. Qayta chaqirilsa — hech narsa qilmaydi."""
    global _LOADED
    if _LOADED and not force:
        return PLUGINS
    _LOADED = True
    from ..tools import load_all

    load_all()  # yadro vositalari birinchi: plugin ularning nomini egallay olmasin
    enabled = _enabled_map()
    for path, builtin in discover():
        pid = path.name
        try:
            if not _ID.match(pid):
                raise ValueError(f"invalid plugin folder name '{pid}' (use a-z, 0-9, _)")
            mod = _load_module(pid, path)
            man = dict(getattr(mod, "MANIFEST", {}) or {})
            if man.get("id", pid) != pid:
                raise ValueError(f"MANIFEST id '{man.get('id')}' must equal folder name '{pid}'")
            info = PluginInfo(id=pid, name=man.get("name", pid), version=str(man.get("version", "0")),
                              description=man.get("description", ""), path=str(path), builtin=builtin,
                              enabled=enabled.get(pid, man.get("enabled_by_default", True)))
            PLUGINS[pid] = info
            _register_settings(info, man.get("settings", []))
            if not info.enabled:
                info.status = "disabled"
                continue
            ctx = PluginContext(info)
            CONTEXTS[pid] = ctx
            setup = getattr(mod, "setup", None)
            if callable(setup):
                setup(ctx)
            info.status = "loaded"
        except Exception as e:  # noqa: BLE001 — bitta plugin boshqalarini va yadroni to'xtatmasin
            info = PLUGINS.get(pid) or PluginInfo(id=pid, name=pid, version="?", description="", path=str(path),
                                                  builtin=builtin)
            PLUGINS[pid] = info
            info.status, info.error = "error", f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"[-1500:]
            registry.remove_source(f"plugin:{pid}")
            BUS.off_owner(pid)
            ctx = CONTEXTS.pop(pid, None)
            if ctx:
                ctx.stop()
            log.warning("plugin %s failed: %s", pid, e)
    return PLUGINS


def attach_runtime(runtime) -> None:
    for ctx in CONTEXTS.values():
        ctx.runtime = runtime
    BUS.emit("startup")


def shutdown_plugins() -> None:
    BUS.emit("shutdown")
    for ctx in CONTEXTS.values():
        ctx.stop()


def routers() -> list[tuple[str, Any]]:
    return [(pid, ctx._router) for pid, ctx in CONTEXTS.items() if ctx._router is not None]
