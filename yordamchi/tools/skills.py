"""O'rgatilgan skill'lar: foydalanuvchi ko'rsatgan qadamlarni yozish va takrorlash (yordamchi/teach.py)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from .. import teach
from .base import Risk, ToolError, registry


def _register_procedure(name: str, app: str) -> None:
    """Agent "burp suite'ni och" deganda bu skill borligini bilishi uchun o'rganish xotirasiga ham yoziladi."""
    from ..learning import Procedure, Procedures, stems

    words = sorted({w for w in stems(f"{name} {app}")} | {app.lower(), "och", "ishga", "tushir", "open", "start"})
    P = Procedures()
    for p in P.load():
        if p.kind == "taught" and p.task == name:
            P.remove(p.id)
    sk = teach.find_skill(name)
    need = teach.placeholders(sk["steps"]) if sk else []
    call = f"run_skill(name='{name}')"
    if need:  # masalan: run_skill(name='telegramda yuborish', params='{"kontakt": "..."}')
        example = json.dumps({n: "..." for n in need}, ensure_ascii=False)
        call = f"run_skill(name='{name}', params='{example}')"
    P.add(Procedure(task=name, app=app or "?", kind="taught", keywords=words[:14],
                    steps=[f"{call} - taught by the user, replays the exact steps"]))


@registry.tool()
def start_teaching(name: Annotated[str, "Short name of the skill, e.g. 'burp suite ishga tushirish'"]) -> dict:
    """Start teach mode: record what the user does with mouse and keyboard (clicks, typing, opened programs)
    until stop_teaching. Use when the user says "o'rgataman", "o'rgatish rejimi", "ko'rsataman qanday qilinadi"."""
    if not name.strip():
        raise ToolError("Give the skill a short name")
    try:
        teach.RECORDER.start(name.strip())
    except RuntimeError as e:
        raise ToolError(str(e)) from e
    return {"recording": True, "name": name.strip(),
            "say": "Tell the user: recording started; show the steps; say 'o'rgatish tugadi' when finished."}


@registry.tool()
def stop_teaching(save: Annotated[bool, "False = discard the recording"] = True) -> dict:
    """Stop teach mode and save the recorded steps as a skill (or discard)."""
    rec = teach.RECORDER
    if not rec.active:
        raise ToolError("Teach mode is not running")
    name = rec.name
    steps = rec.stop()
    if not save:
        return {"discarded": True, "name": name}
    if not steps:
        return {"ok": False, "name": name, "error": "No steps were recorded (no clicks or typing seen)."}
    path = teach.save_skill(name, steps)
    app = json.loads(Path(path).read_text(encoding="utf-8")).get("app", "")
    _register_procedure(name, app)
    return {"saved": name, "steps": len(steps), "summary": [teach.describe(s) for s in steps][:12],
            "secret_steps": sum(1 for s in steps if s.get("secret"))}


@registry.tool(timeout=300)
def run_skill(name: Annotated[str, "Name of a taught skill"],
              start_step: Annotated[int, "Continue from this step (after a paused_for_secret)"] = 0,
              params: Annotated[str, "JSON object with values for the skill's {placeholders}, "
                                     "e.g. {\"kontakt\": \"Ali\"}"] = "") -> dict:
    """Run a skill the user taught (exact recorded steps: open program, clicks, typing). Prefer it over
    improvising when a taught skill matches the request. Skills with placeholders need `params`."""
    sk = teach.find_skill(name)
    if not sk:
        raise ToolError(f"No taught skill matches '{name}'. Known: {[s['name'] for s in teach.load_skills()][:10]}")
    values: dict = params if isinstance(params, dict) else {}  # model ba'zan obyektni to'g'ridan-to'g'ri beradi
    if isinstance(params, str) and params.strip():
        try:
            values = json.loads(params)
        except json.JSONDecodeError as e:
            raise ToolError(f"params must be a JSON object: {e.msg}") from e
        if not isinstance(values, dict):
            raise ToolError("params must be a JSON object")
    res = teach.run_steps(sk["steps"], start=max(0, start_step), params=values)
    if res.get("ok"):
        sk["uses"] = sk.get("uses", 0) + 1
        path = Path(sk.pop("_path"))
        path.write_text(json.dumps(sk, ensure_ascii=False, indent=1), encoding="utf-8")
    elif "failed_step" in res:
        res["failed_step_description"] = teach.describe(sk["steps"][res["failed_step"]])
        res["hint"] = ("Tell the user which step failed; they can re-teach it with start_teaching "
                       "using the same name.") if not res.get("paused_for_secret") else res.get("note")
    return {"skill": sk["name"], **res}


@registry.tool()
def list_taught_skills() -> dict:
    """List skills the user has taught (name, app, number of steps, how often used)."""
    return {"skills": [{"name": s["name"], "app": s.get("app"), "steps": len(s.get("steps", [])),
                        "uses": s.get("uses", 0), "params": teach.placeholders(s.get("steps", []))} for s in teach.load_skills()],
            "teaching_now": teach.RECORDER.name if teach.RECORDER.active else None}


@registry.tool(risk=Risk.CONFIRM, confirm=lambda a: f"'{a.get('name')}' skillini o'chirish")
def delete_skill(name: Annotated[str, "Name of the taught skill to delete"]) -> dict:
    """Delete a taught skill."""
    sk = teach.find_skill(name)
    if not sk:
        raise ToolError(f"No taught skill matches '{name}'")
    Path(sk["_path"]).unlink(missing_ok=True)
    from ..learning import Procedures

    P = Procedures()
    for p in P.load():
        if p.kind == "taught" and p.task == sk["name"]:
            P.remove(p.id)
    return {"deleted": sk["name"]}
