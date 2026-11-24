"""Ovozli rejimni ishga tushirish va audio yordamchi buyruqlar (ovozlar, qurilmalar, TTS sinovi)."""

from __future__ import annotations

import asyncio
import os
import time
from concurrent.futures import TimeoutError as FutTimeout

from ..config import Settings
from ..runtime import Runtime
from ..safety import MOD_ALT, MOD_CONTROL, GlobalHotkey
from .assistant import VoiceAssistant, VoiceConfig


def list_devices() -> None:
    import sounddevice as sd

    for i, d in enumerate(sd.query_devices()):
        kinds = ("in" if d["max_input_channels"] else "") + ("/out" if d["max_output_channels"] else "")
        print(f"{i:3}  {kinds:7} {d['name']}")
    print(f"default in/out: {sd.default.device}")


def list_voices(settings: Settings) -> None:
    from .speech import GatewaySpeech

    async def go():
        s = GatewaySpeech(settings)
        try:
            for v in await s.voices():
                print(f"  {v.get('name'):28} {','.join(v.get('languages', []))}")
        finally:
            await s.aclose()
    asyncio.run(go())


def say(settings: Settings, text: str, cfg: VoiceConfig) -> None:
    from .audio import Player
    from .speech import TTS_RATE, GatewaySpeech

    async def go():
        s = GatewaySpeech(settings, voice=cfg.voice, speed=cfg.speed)
        p = Player(rate=TTS_RATE, device=cfg.output_device)
        t = time.perf_counter()
        first = None

        async def timed():
            nonlocal first
            async for c in s.tts_stream(text):
                if first is None:
                    first = (time.perf_counter() - t) * 1000
                yield c
        try:
            await p.play(timed())
            print(f"birinchi audio {first:.0f} ms, jami {(time.perf_counter() - t) * 1000:.0f} ms")
        finally:
            p.close()
            await s.aclose()
    asyncio.run(go())


def run_voice(settings: Settings, cfg: VoiceConfig, on_event, status, api: bool = True,
              open_panel: bool = False) -> None:
    from ..settings import STORE

    va = VoiceAssistant(settings, cfg, status=status)
    rt = Runtime(settings, confirm=va.confirm, on_event=on_event, kill_hotkey=True)
    va.attach(rt)
    if api:
        from ..api.server import start_server

        try:
            url = start_server(runtime=rt, assistant=va)
            from ..api.server import STATE as API_STATE

            note = API_STATE.get("port_note")
            status(f"Boshqaruv paneli: {url}" + (f"  ({note})" if note else ""))
        except RuntimeError as e:
            url = None
            status(f"[!] Boshqaruv paneli ishga tushmadi: {e}")
        if open_panel and url:
            import webbrowser

            webbrowser.open(url)
    key = (STORE.get("voice.ptt_key") or "J").strip().upper()
    ptt = GlobalHotkey(va.trigger_threadsafe, modifiers=MOD_CONTROL | MOD_ALT, vk=ord(key[0]))
    ptt.start()
    ptt.registered.wait(2)
    if ptt.error:
        status(f"Ctrl+Alt+{key} ro'yxatdan o'tmadi (boshqa dastur egallagan bo'lishi mumkin): {ptt.error}")
    else:
        status(f"Push-to-talk: Ctrl+Alt+{key}")
    if rt.hotkey and rt.hotkey.error:
        status(f"Kill switch ro'yxatdan o'tmadi: {rt.hotkey.error}")
    fut = asyncio.run_coroutine_threadsafe(va.run(), rt.loop)
    try:
        while True:
            try:
                fut.result(timeout=0.2)
                break
            except FutTimeout:
                continue
    except KeyboardInterrupt:
        status("Chiqilmoqda...")
    finally:
        fut.cancel()
        ptt.stop()
        time.sleep(0.3)
        rt.shutdown()
