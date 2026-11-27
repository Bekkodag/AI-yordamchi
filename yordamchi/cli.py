"""Matnli interfeys (1-bosqich). Ovoz 2-bosqichda shu Runtime ustiga quriladi.

    python -m yordamchi                 # interaktiv (matn)
    python -m yordamchi --once "chrome'ni och"
    python -m yordamchi --voice         # ovozli rejim ("Asalim" yoki Ctrl+Alt+J)
    python -m yordamchi --say "Salom"   # TTS sinovi
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import queue
import sys
import time
import webbrowser
from concurrent.futures import TimeoutError as FutTimeout
from dataclasses import replace

from rich.console import Console

from .agent import Event, Failed, Reply, ToolFinished, ToolStarted
from .config import NotConfigured, Settings, load_env
from .runtime import Runtime

console = Console(highlight=False)


def _short(d: dict, n: int = 160) -> str:
    s = json.dumps(d, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[: n - 1] + "…"


def print_event(ev: Event) -> None:
    if isinstance(ev, ToolStarted):
        console.print(f"  [dim]→ {ev.name}({_short(ev.args, 120)})[/dim]")
    elif isinstance(ev, ToolFinished):
        ok = ev.result.get("ok", True)
        color = "green" if ok else "yellow"
        console.print(f"  [{color}]← {ev.name} {ev.ms:.0f} ms[/{color}] [dim]{_short(ev.result)}[/dim]")
    elif isinstance(ev, Reply):
        console.print(f"[bold cyan]JARVIS:[/bold cyan] {ev.text}  [dim]({ev.steps} qadam, {ev.ms:.0f} ms)[/dim]")
    elif isinstance(ev, Failed):
        console.print(f"[bold red]Xato:[/bold red] {ev.error}")


class ConsoleConfirm:
    """Tasdiq so'rovlari fon loop'dan asosiy oqimga (input) uzatiladi."""

    def __init__(self, auto: bool | None = None) -> None:
        self.auto = auto
        self.requests: queue.Queue = queue.Queue()

    async def __call__(self, text: str) -> bool:
        if self.auto is not None:
            console.print(f"  [magenta]? Tasdiq: {text} -> {'ha (auto)' if self.auto else 'yo`q (auto)'}[/magenta]")
            return self.auto
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self.requests.put((text, loop, fut))
        return await fut

    def serve_pending(self) -> None:
        while True:
            try:
                text, loop, fut = self.requests.get_nowait()
            except queue.Empty:
                return
            console.print(f"  [bold magenta]? Tasdiqlaysizmi:[/bold magenta] {text}")
            try:
                ans = input("    [ha/yo'q] > ").strip().lower()
            except EOFError:
                ans = ""
            loop.call_soon_threadsafe(fut.set_result, ans in ("ha", "h", "yes", "y", "да", "ok"))


def run_turn(rt: Runtime, confirm: ConsoleConfirm, text: str) -> None:
    fut = rt.submit(text)
    while True:
        try:
            fut.result(timeout=0.1)
            return
        except FutTimeout:
            confirm.serve_pending()
        except KeyboardInterrupt:
            rt.cancel()
            console.print("[yellow]To'xtatildi.[/yellow]")
            try:
                fut.result(timeout=5)
            except Exception:  # noqa: BLE001
                pass
            return


def print_skills() -> None:
    from .learning import Procedures
    from .tools import load_all, memory

    reg = load_all()
    items = Procedures().load()
    from .teach import load_skills

    tasks = [p for p in items if p.kind == "task"]
    quirks = [p for p in items if p.kind == "quirk"]
    taught = load_skills()
    facts = memory.load()
    print(f"Tayyor vositalar (kodda): {len(reg._tools)}")
    print(f"Siz o'rgatgan skill'lar:  {len(taught)}")
    print(f"O'rganilgan retseptlar:   {len(tasks)}")
    print(f"Ilova o'ziga xosliklari:  {len(quirks)}")
    print(f"Eslab qolingan faktlar:   {len(facts)}")
    for title, rows in (("\nSiz o'rgatgan skill'lar:", [f"{t['name']} ({t.get('app')}, {len(t['steps'])} qadam, "
                                                         f"ishlatilgan: {t.get('uses', 0)})" for t in taught]),
                        ("\nIlova o'ziga xosliklari (har safar avtomatik):", [f"{p.app}: {p.task}" for p in quirks]),
                        ("\nRetseptlar:", [f"[{p.app}] {p.task}  (ishlatilgan: {p.uses})" for p in
                                           sorted(tasks, key=lambda x: (x.app, x.task))]),
                        ("\nFaktlar:", facts)):
        if rows:
            print(title)
            for r in rows:
                print("  -", r)


def _panel_only(open_browser: bool) -> None:
    """LLM sozlanmagan bo'lsa ham panel ishlaydi: sozlamalarni kiritib, keyin qayta ishga tushirish."""
    from .api.server import start_server

    try:
        url = start_server(runtime=None)
    except RuntimeError as e:
        console.print(f"[red]Boshqaruv paneli ishga tushmadi: {e}[/red]")
        return
    from .api.server import STATE as API_STATE

    note = API_STATE.get("port_note")
    console.print(f"[bold]Boshqaruv paneli:[/bold] {url}  (to'xtatish: Ctrl+C)" + (f"  ({note})" if note else ""))
    if open_browser:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def main() -> None:
    if sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    load_env()  # sozlamalar default'lari argparse'dan oldin kerak
    from .settings import STORE

    g = STORE.get
    ap = argparse.ArgumentParser(prog="yordamchi")
    ap.add_argument("--once", help="Bitta buyruqni bajarib chiqish")
    ap.add_argument("--yes", action="store_true", help="--once rejimida tasdiqlarga avtomatik 'ha'")
    ap.add_argument("--model", help="Gateway alias (default: fast)")
    v = ap.add_argument_group("ovoz")
    v.add_argument("--voice", action="store_true", help="Ovozli rejim")
    v.add_argument("--no-wake", action="store_true", help="Wake word o'chiq, faqat Ctrl+Alt+J")
    v.add_argument("--no-follow-up", action="store_true", help="Javobdan keyin qayta tinglamasin")
    v.add_argument("--wake-threshold", type=float, default=float(g("voice.wake_threshold")))
    v.add_argument("--wake-word", default=str(g("voice.wake_word") or g("assistant.name") or "Asalim"),
                   help="Ovozni uyg'otuvchi istalgan nom yoki so'z")
    v.add_argument("--no-stt-wake", action="store_true",
                   help="Kutishda sozlangan wake wordni STT orqali tekshirmasin")
    v.add_argument("--follow-up-ms", type=int, default=int(g("voice.follow_up_ms")))
    v.add_argument("--no-duck", action="store_true", help="Suhbat paytida boshqa ilovalar ovozini pasaytirmasin")
    v.add_argument("--duck-level", type=float, default=float(g("voice.duck_level")),
                   help="Suhbat paytida boshqa ilovalar ovozi (0.3 = 30%%)")
    v.add_argument("--voice-name", default=g("speech.voice") or None, help="TTS ovozi (--list-voices)")
    v.add_argument("--speed", type=float, default=float(g("speech.speed")))
    v.add_argument("--input-device", default=g("voice.input_device") or None)
    v.add_argument("--output-device", default=g("voice.output_device") or None)
    v.add_argument("--no-aec", action="store_true", help="Echo cancellation o'chiq")
    v.add_argument("--list-voices", action="store_true")
    v.add_argument("--list-devices", action="store_true")
    v.add_argument("--say", help="Matnni TTS bilan aytib ko'rish")
    ap.add_argument("--skills", action="store_true", help="Bilgan vositalar, o'rgangan retseptlar va faktlar")
    ap.add_argument("--panel", action="store_true", help="Faqat boshqaruv paneli (sozlash uchun)")
    ap.add_argument("--no-api", action="store_true", help="Boshqaruv panelini ishga tushirmasin")
    ap.add_argument("--open", action="store_true", help="Panelni brauzerda ochish")
    args = ap.parse_args()

    if args.skills:
        print_skills()
        return
    if args.list_devices:
        from .voice.main import list_devices
        list_devices()
        return

    from . import plugins

    plugins.load_all_plugins()
    if args.panel:
        _panel_only(args.open)
        return
    try:
        settings = Settings.load()
    except NotConfigured as e:
        console.print(f"[yellow]{e}[/yellow]\nBoshqaruv paneli ochilmoqda — sozlamalarni kiriting.")
        _panel_only(True)
        return
    if args.model:
        settings = replace(settings, model=args.model)

    if args.list_voices or args.say or args.voice:
        from .voice.assistant import VoiceConfig
        from .voice.main import list_voices, run_voice, say

        def dev(x):
            return int(x) if x and str(x).isdigit() else (x or None)

        vcfg = VoiceConfig(
            wake=not args.no_wake, wake_word=args.wake_word, wake_threshold=args.wake_threshold,
            follow_up=not args.no_follow_up,
            stt_wake=not args.no_stt_wake, follow_up_timeout_ms=args.follow_up_ms,
            duck=not args.no_duck and bool(g("voice.duck")), duck_level=args.duck_level,
            voice=args.voice_name, speed=args.speed, aec=not args.no_aec and bool(g("voice.aec")),
            input_device=dev(args.input_device), output_device=dev(args.output_device),
        )
        if args.list_voices:
            list_voices(settings)
        elif args.say:
            say(settings, args.say, vcfg)
        else:
            run_voice(settings, vcfg, on_event=print_event, api=not args.no_api and bool(g("api.enabled")),
                      open_panel=args.open,
                      status=lambda s: console.print(f"[dim]{s}[/dim]" if s.startswith(("[", " ")) else s))
        return

    confirm = ConsoleConfirm(auto=(True if args.yes else False) if args.once else None)
    rt = Runtime(settings, confirm=confirm, on_event=print_event, kill_hotkey=not args.once)
    if not args.once and not args.no_api and g("api.enabled"):
        from .api.server import start_server

        try:
            url = start_server(runtime=rt)
            from .api.server import STATE as API_STATE

            note = API_STATE.get("port_note")
            console.print(f"[dim]Boshqaruv paneli: {url}[/dim]" + (f"  [yellow]({note})[/yellow]" if note else ""))
        except RuntimeError as e:
            url = None
            console.print(f"[yellow]Boshqaruv paneli ishga tushmadi: {e}[/yellow]")
        if args.open and url:
            webbrowser.open(url)

    if args.once:
        run_turn(rt, confirm, args.once)
        rt.shutdown()
        return

    hk = rt.hotkey
    hk_info = "Ctrl+Alt+X - to'xtatish" if hk and not hk.error else f"hotkey yo'q: {hk.error if hk else '-'}"
    console.print(f"[bold]{settings.assistant_name}[/bold] [dim]({settings.gateway_url}, model={settings.model}; "
                  f"{hk_info}; "
                  f"Ctrl+C - to'xtatish; /reset, /tools, /exit)[/dim]")
    try:
        while True:
            try:
                text = input("\nSiz> ").strip()
            except EOFError:
                break
            except KeyboardInterrupt:
                continue
            if not text:
                continue
            if text in ("/exit", "/quit", "chiq"):
                break
            if text == "/reset":
                rt.agent.reset()
                console.print("[dim]Suhbat tarixi tozalandi.[/dim]")
                continue
            if text == "/tools":
                for t in rt.agent.tools.all():
                    console.print(f"  {t.name:18} [dim]{t.risk.value:8} {t.description.splitlines()[0]}[/dim]")
                continue
            run_turn(rt, confirm, text)
    finally:
        rt.shutdown()


if __name__ == "__main__":
    main()
