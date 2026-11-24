"""Suhbat boshqaruvi."""

from __future__ import annotations

from .base import registry


@registry.tool()
def end_conversation() -> dict:
    """End the voice conversation: JARVIS stops listening and waits for the wake word again.
    Call when the user says they are done ("bo'ldi", "rahmat", "kutish rejimiga o't", "seni chaqirmadim")."""
    return {"end_conversation": True}
