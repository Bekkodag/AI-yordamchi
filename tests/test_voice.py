from __future__ import annotations

import numpy as np

from yordamchi.voice.assistant import is_stop, parse_yes, strip_wake
from yordamchi.voice.audio import FRAME_BYTES, EndpointConfig, Endpointer


def test_strip_wake():
    assert strip_wake("Jarvis, chromeni och") == "chromeni och"
    assert strip_wake("hey jarvis ovozni oshir") == "ovozni oshir"
    assert strip_wake("djarvis telegramni yop") == "telegramni yop"
    assert strip_wake("джарвис открой хром") == "открой хром"
    assert strip_wake("chromeni och") == "chromeni och"


def test_stop_and_yes_no():
    assert is_stop("To'xta!") and is_stop("bas") and not is_stop("to'xtatib qo'y musiqani")
    assert parse_yes("ha") and parse_yes("Ha, albatta") and parse_yes("mayli")
    assert not parse_yes("yo'q") and not parse_yes("kerak emas") and not parse_yes("hmm")
    assert not parse_yes("ha yo'q")  # ziddiyat -> yo'q


def _frames(pcm: bytes):
    for i in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES):
        yield pcm[i:i + FRAME_BYTES]


def test_endpointer_timeout_on_silence():
    ep = Endpointer(EndpointConfig(start_timeout_ms=500))
    res = None
    for f in _frames(bytes(16000 * 2)):
        res = ep.feed(f)
        if res:
            break
    assert res == "timeout"


def test_endpointer_ignores_tone():
    ep = Endpointer(EndpointConfig(start_timeout_ms=1500))
    t = np.arange(16000) / 16000
    tone = (np.sin(2 * np.pi * 880 * t) * 8000).astype(np.int16).tobytes()
    res = None
    for f in _frames(tone + bytes(16000 * 2)):
        res = ep.feed(f)
        if res:
            break
    assert res == "timeout"
