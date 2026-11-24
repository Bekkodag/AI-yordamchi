"""Tool registry: Python funksiyalardan OpenAI tool sxemasi yasaydi, xavf darajasini boshqaradi."""

from __future__ import annotations

import inspect
import json
import types
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any, Callable, Literal, Union, get_args, get_origin, get_type_hints


class Risk(str, Enum):
    SAFE = "safe"        # o'zi bajaradi
    CONFIRM = "confirm"  # foydalanuvchidan tasdiq so'raydi


class ToolError(Exception):
    """Foydalanuvchiga/LLM'ga tushunarli xato (traceback'siz)."""


@dataclass
class Tool:
    name: str
    description: str
    fn: Callable[..., Any]
    parameters: dict
    risk: Risk = Risk.SAFE
    confirm: Callable[[dict], str] | None = None      # tasdiq matni
    confirm_if: Callable[[dict], bool] | None = None  # None -> CONFIRM bo'lsa har doim
    timeout: float = 60.0
    source: str = "core"        # "core" | "plugin:<id>" | "user:<id>" (paneldan yaratilgan)

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}

    def needs_confirm(self, args: dict) -> bool:
        if self.risk is not Risk.CONFIRM:
            return False
        return self.confirm_if(args) if self.confirm_if else True

    def confirm_text(self, args: dict) -> str:
        if self.confirm:
            try:
                return self.confirm(args)
            except Exception:  # noqa: BLE001
                pass
        return f"{self.name}({json.dumps(args, ensure_ascii=False)})"

    def prepare(self, args: dict) -> dict:
        """Argumentlarni sxemaga moslaydi (LLM ba'zan "30" yuboradi), majburiylarini tekshiradi."""
        props = self.parameters.get("properties", {})
        out: dict[str, Any] = {}
        unknown = [k for k in (args or {}) if k not in props]
        if unknown:  # jimgina tashlab yuborsak model "bajarildi" deb o'ylaydi (logda type_text + press_keys)
            raise ToolError(f"Unknown argument(s): {', '.join(unknown)}. Valid: {', '.join(props) or 'none'}. "
                            "Use a separate tool call for other actions (e.g. press_keys for shortcuts).")
        for k, v in (args or {}).items():
            out[k] = _coerce(v, props[k])
        missing = [r for r in self.parameters.get("required", []) if r not in out]
        if missing:
            raise ToolError(f"Missing required arguments: {', '.join(missing)}")
        return out

    def invoke(self, args: dict) -> dict:
        try:
            res = self.fn(**self.prepare(args))
        except ToolError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if isinstance(res, dict):
            return {"ok": True, **res} if "ok" not in res else res
        return {"ok": True, "result": res}


def _coerce(v: Any, schema: dict) -> Any:
    t = schema.get("type")
    try:
        if t == "integer" and not isinstance(v, bool):
            return int(float(v))
        if t == "number":
            return float(v)
        if t == "boolean" and isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "ha")
        if t == "array" and isinstance(v, str):
            s = v.strip()
            if s.startswith("["):
                return json.loads(s)
            return [s]
    except (ValueError, TypeError, json.JSONDecodeError):
        return v
    return v


def _type_schema(t: Any) -> dict:
    origin = get_origin(t)
    if origin is Annotated:
        return _type_schema(get_args(t)[0])
    if origin in (Union, types.UnionType):
        args = [a for a in get_args(t) if a is not type(None)]
        return _type_schema(args[0]) if args else {"type": "string"}
    if origin is Literal:
        vals = list(get_args(t))
        return {"type": "integer" if isinstance(vals[0], int) else "string", "enum": vals}
    if origin in (list, tuple, set):
        item = get_args(t)[0] if get_args(t) else str
        return {"type": "array", "items": _type_schema(item)}
    return {str: {"type": "string"}, int: {"type": "integer"}, float: {"type": "number"},
            bool: {"type": "boolean"}}.get(t, {"type": "string"})


def build_parameters(fn: Callable) -> dict:
    hints = get_type_hints(fn, include_extras=True)
    props: dict[str, dict] = {}
    required: list[str] = []
    for pname, p in inspect.signature(fn).parameters.items():
        hint = hints.get(pname, str)
        desc = None
        if get_origin(hint) is Annotated:
            desc = next((m for m in get_args(hint)[1:] if isinstance(m, str)), None)
        sch = _type_schema(hint)
        if desc:
            sch["description"] = desc
        if p.default is inspect.Parameter.empty:
            required.append(pname)
        elif p.default is not None:
            sch["default"] = p.default
        props[pname] = sch
    return {"type": "object", "properties": props, "required": required}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self.disabled: set[str] = set()       # paneldan o'chirilgan vositalar (modelga ko'rsatilmaydi)
        self.risk_override: dict[str, Risk] = {}

    def tool(self, *, risk: Risk = Risk.SAFE, confirm: Callable[[dict], str] | None = None,
             confirm_if: Callable[[dict], bool] | None = None, timeout: float = 60.0,
             name: str | None = None, source: str = "core", description: str | None = None,
             parameters: dict | None = None):
        def deco(fn: Callable) -> Callable:
            t = Tool(
                name=name or fn.__name__,
                description=description or inspect.getdoc(fn) or fn.__name__,
                fn=fn,
                parameters=parameters or build_parameters(fn),
                risk=risk, confirm=confirm, confirm_if=confirm_if, timeout=timeout, source=source,
            )
            old = self._tools.get(t.name)
            if old is not None and old.source != t.source:
                raise ToolError(f"Tool name '{t.name}' is already used by {old.source}")
            self._tools[t.name] = t
            return fn
        return deco

    def add(self, t: Tool) -> None:
        old = self._tools.get(t.name)
        if old is not None and old.source != t.source:
            raise ToolError(f"Tool name '{t.name}' is already used by {old.source}")
        self._tools[t.name] = t

    def remove_source(self, source: str) -> int:
        names = [n for n, t in self._tools.items() if t.source == source]
        for n in names:
            del self._tools[n]
        return len(names)

    def get(self, name: str) -> Tool | None:
        t = self._tools.get(name)
        if t is None or name in self.disabled:
            return None
        if name in self.risk_override and t.risk is not self.risk_override[name]:
            from dataclasses import replace

            # Faqat xavf darajasini OSHIRISH mumkin (har doim tasdiq so'rasin); pasaytirish API'da taqiqlangan
            t = replace(t, risk=self.risk_override[name], confirm_if=None)
        return t

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def schemas(self) -> list[dict]:
        return [t.schema() for n, t in self._tools.items() if n not in self.disabled]


registry = ToolRegistry()
