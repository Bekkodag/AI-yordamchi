"""OpenAI-mos LLM va nutq serveri uchun HTTP klientlar.

Ichki tarmoqda server bir nomda ishlab, sertifikat boshqa nom uchun berilgan bo'lishi mumkin
(reverse proxy). `llm.tls_server_name` berilsa: Host header URL'dagi nom bo'lib qoladi, SNI va
sertifikat tekshiruvi shu nom bo'yicha bajariladi — tekshiruv o'chirilmaydi.
"""

from __future__ import annotations

import httpx2
from openai import AsyncOpenAI, DefaultAsyncHttpxClient

from .config import Settings


class _SNIMixin:
    _tls_name: str | None = None

    def build_request(self, *args, **kwargs):
        if self._tls_name:
            ext = dict(kwargs.get("extensions") or {})
            ext.setdefault("sni_hostname", self._tls_name)
            kwargs["extensions"] = ext
        return super().build_request(*args, **kwargs)  # type: ignore[misc]


class GatewayAsyncHttp(_SNIMixin, DefaultAsyncHttpxClient):
    def __init__(self, *, tls_name: str | None = None, **kw):
        super().__init__(**kw)
        self._tls_name = tls_name


def make_http(settings: Settings, timeout: float = 120.0) -> GatewayAsyncHttp:
    return GatewayAsyncHttp(
        tls_name=settings.tls_name,
        verify=settings.verify,
        timeout=httpx2.Timeout(timeout, connect=10.0),
    )


def make_llm(settings: Settings) -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=settings.llm_base,
        api_key=settings.gateway_token,
        http_client=make_http(settings),
        max_retries=2,
    )
