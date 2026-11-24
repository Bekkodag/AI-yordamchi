"""Brauzer: URL ochish, qidiruv."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from typing import Annotated, Literal

import httpx

from .base import ToolError, registry

_SEARCH = {
    "google": "https://www.google.com/search?q={}",
    "youtube": "https://www.youtube.com/results?search_query={}",
    "maps": "https://www.google.com/maps/search/{}",
    "wikipedia": "https://uz.wikipedia.org/w/index.php?search={}",
}


@registry.tool()
def open_url(url: Annotated[str, "Web address, e.g. 'kun.uz' or 'https://github.com'"]) -> dict:
    """Open a website in the default browser."""
    u = url.strip()
    if " " in u or "." not in u:
        raise ToolError("Not a URL; use web_search for search queries")
    if not u.lower().startswith(("http://", "https://")):
        u = "https://" + u
    if urllib.parse.urlparse(u).scheme not in ("http", "https"):
        raise ToolError("Only http/https URLs")
    os.startfile(u)  # noqa: S606
    return {"opened": u}


@registry.tool()
def web_search(query: str, site: Literal["google", "youtube", "maps", "wikipedia"] = "google") -> dict:
    """Open search results in the browser (only shows a results list, does not play anything).
    To play a song or video use youtube_play."""
    u = _SEARCH[site].format(urllib.parse.quote_plus(query))
    os.startfile(u)  # noqa: S606
    return {"opened": u}


@registry.tool()
def youtube_search(query: Annotated[str, "What to search on YouTube"]) -> dict:
    """Open YouTube search results for a query in a new browser tab (does not play). To play use youtube_play."""
    q = query.strip()
    if not q:
        raise ToolError("Empty query")
    u = _SEARCH["youtube"].format(urllib.parse.quote_plus(q))
    os.startfile(u)  # noqa: S606
    return {"opened": u, "query": q}


_VIDEO = re.compile(r'"videoRenderer":\{"videoId":"([\w-]{11})".{0,1500}?"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"',
                    re.DOTALL)


def find_youtube_videos(query: str, limit: int = 5) -> list[dict]:
    r = httpx.get("https://www.youtube.com/results", params={"search_query": query, "hl": "uz"}, timeout=10,
                  headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                         "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
                           "Accept-Language": "uz,ru;q=0.8,en;q=0.6"},
                  cookies={"CONSENT": "YES+1"}, follow_redirects=True)
    r.raise_for_status()
    out, seen = [], set()
    for vid, title in _VIDEO.findall(r.text):
        if vid in seen:
            continue
        seen.add(vid)
        try:
            title = json.loads(f'"{title}"')
        except json.JSONDecodeError:
            pass
        out.append({"id": vid, "title": title})
        if len(out) >= limit:
            break
    return out


@registry.tool(timeout=45)
def youtube_play(query: Annotated[str, "Song or video to play, e.g. 'G'iyosbek Narzullayev Bu muhabbat tarixi'"]) -> dict:
    """Find a song/video on YouTube and PLAY it in the browser. Verifies that sound actually starts."""
    from . import _input
    from .audio_sessions import browser_playing
    from .windows import bring_to_front, find_windows

    videos = find_youtube_videos(query)
    if not videos:
        raise ToolError(f"Nothing found on YouTube for '{query}'")
    v = videos[0]
    already = browser_playing(0.5)
    os.startfile(f"https://www.youtube.com/watch?v={v['id']}")  # noqa: S606
    playing = False
    for _ in range(10):                      # ~8 s: avtoplay
        time.sleep(0.8)
        if browser_playing(0.5) and not already:
            playing = True
            break
    if not playing:                          # avtoplay bloklangan bo'lsa — YouTube'ning 'k' tugmasi
        wins = [w for w in find_windows("YouTube") if w.process.lower() in ("chrome.exe", "msedge.exe", "firefox.exe",
                                                                             "brave.exe", "opera.exe")]
        if wins and bring_to_front(wins[0].hwnd):
            time.sleep(0.3)
            _input.press(_input.vk_for("k"))
            for _ in range(5):
                time.sleep(0.6)
                if browser_playing(0.5):
                    playing = True
                    break
    return {"ok": playing, "title": v["title"], "url": f"https://www.youtube.com/watch?v={v['id']}",
            "sound_verified": playing,
            # musiqa chalinayotganda mikrofon uni buyruq deb eshitmasligi uchun suhbat tugaydi
            "end_conversation": playing,
            "other_results": [x["title"] for x in videos[1:4]],
            **({} if playing else {"hint": "Video page opened but no sound detected. Say so honestly."})}
