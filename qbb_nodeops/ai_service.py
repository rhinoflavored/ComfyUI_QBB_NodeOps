"""Optional, user-triggered OpenAI-compatible AI helpers for QBB NodeOps.

No request is made until a user presses an AI button.  The API key is stored
locally in the plugin directory and is never returned to the browser after it
has been saved.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import threading
from datetime import UTC, datetime, timedelta
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .settings import proxy_url


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
AI_SETTINGS_PATH = PLUGIN_ROOT / "qbb_nodeops_ai_settings.json"
_LOCK = threading.RLock()
_CACHE: dict[str, Any] | None = None
_DEFAULTS = {
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4.1-mini",
    "api_key": "",
}


def _normalise(data: dict[str, Any] | None) -> dict[str, str]:
    values = dict(_DEFAULTS)
    if isinstance(data, dict):
        values.update({key: str(data[key]) for key in _DEFAULTS if key in data and data[key] is not None})
    values["base_url"] = values["base_url"].strip().rstrip("/") or _DEFAULTS["base_url"]
    values["model"] = values["model"].strip() or _DEFAULTS["model"]
    values["api_key"] = values["api_key"].strip()
    return values


def _load_raw() -> dict[str, str]:
    global _CACHE
    with _LOCK:
        if _CACHE is not None:
            return dict(_CACHE)
        data: dict[str, Any] | None = None
        try:
            if AI_SETTINGS_PATH.is_file():
                data = json.loads(AI_SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[QBB NodeOps] Failed to read AI settings: {e}", flush=True)
        _CACHE = _normalise(data)
        return dict(_CACHE)


def public_settings() -> dict[str, Any]:
    data = _load_raw()
    return {
        "base_url": data["base_url"],
        "model": data["model"],
        "api_key_configured": bool(data["api_key"]),
    }


def save_ai_settings(patch: dict[str, Any]) -> dict[str, Any]:
    """Save non-secret fields and retain a secret if the UI leaves it blank."""
    global _CACHE
    with _LOCK:
        current = dict(_CACHE) if _CACHE is not None else _load_raw()
        if "base_url" in patch:
            current["base_url"] = str(patch["base_url"] or "")
        if "model" in patch:
            current["model"] = str(patch["model"] or "")
        if patch.get("clear_api_key"):
            current["api_key"] = ""
        elif str(patch.get("api_key") or "").strip():
            current["api_key"] = str(patch["api_key"]).strip()
        current = _normalise(current)
        AI_SETTINGS_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _CACHE = current
        return public_settings()


def _json_request(url: str, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "QBB-NodeOps/experimental",
        },
        method="POST",
    )
    handlers: list[Any] = []
    configured_proxy = proxy_url()
    if configured_proxy:
        handlers.append(urllib.request.ProxyHandler({"http": configured_proxy, "https": configured_proxy}))
    try:
        # A diagnostic answer should be quick.  Do not leave ComfyUI's UI
        # waiting indefinitely when a provider or proxy stalls.
        with urllib.request.build_opener(*handlers).open(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        details = e.read().decode("utf-8", errors="replace")[:600]
        raise RuntimeError(f"AI service HTTP {e.code}: {details}") from e
    except Exception as e:
        raise RuntimeError(f"AI service request failed: {e}") from e


def _chat(system: str, user: str) -> str:
    settings = _load_raw()
    if not settings["api_key"]:
        raise ValueError("Configure an AI API key first")
    data = _json_request(
        settings["base_url"] + "/chat/completions",
        {
            "model": settings["model"],
            "temperature": 0.2,
            "max_tokens": 900,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        },
        settings["api_key"],
    )
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError("AI service returned an unexpected response") from e
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("AI service returned an empty response")
    return text.strip()


def analyse_issue(issue: dict[str, Any], language: str = "zh") -> dict[str, str]:
    name = str(issue.get("name") or "Unknown plugin")[:300]
    reason = str(issue.get("reason") or "")[:4000]
    trace = str(issue.get("traceback") or "")[:6000]
    partial = issue.get("partial_issues")
    if isinstance(partial, list):
        trace += "\n\nPartial issues:\n" + "\n".join(str(x)[:2000] for x in partial[:12])
    locale = "Chinese" if language.lower().startswith("zh") else "English"
    system = (
        "You are a careful ComfyUI custom-node support assistant. Give a concise, actionable diagnosis. "
        "Do not claim a command is safe if uncertain. Separate likely cause, checks, and repair steps. "
        f"Answer in {locale}."
    )
    user = (
        f"Plugin: {name}\nReason: {reason}\nTraceback/details:\n{trace}\n\n"
        f"Runtime: Python {sys.version.split()[0]}, OS {platform.platform()}\n"
        "Explain likely root cause, exact verification steps, and the safest repair options."
    )
    return {"result": _chat(system, user), "plugin": name}


def _github_candidates(query: str) -> list[dict[str, str]]:
    # GitHub search makes the discovery result grounded in current public data.
    search = "ComfyUI " + query
    url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
        {"q": search, "sort": "updated", "order": "desc", "per_page": "10"}
    )
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "QBB-NodeOps/experimental"})
    handlers: list[Any] = []
    configured_proxy = proxy_url()
    if configured_proxy:
        handlers.append(urllib.request.ProxyHandler({"http": configured_proxy, "https": configured_proxy}))
    try:
        with urllib.request.build_opener(*handlers).open(request, timeout=25) as response:
            raw = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as e:
        raise RuntimeError(f"GitHub candidate search failed: {e}") from e
    candidates = []
    for item in raw.get("items", [])[:10]:
        candidates.append({
            "name": str(item.get("full_name") or ""),
            "url": str(item.get("html_url") or ""),
            "description": str(item.get("description") or ""),
            "stars": str(item.get("stargazers_count") or 0),
            "updated_at": str(item.get("updated_at") or ""),
        })
    return candidates


def recommend_plugins(query: str, language: str = "zh") -> dict[str, Any]:
    query = str(query or "").strip()
    if not query:
        raise ValueError("Enter the capability you need")
    candidates = _github_candidates(query)
    locale = "Chinese" if language.lower().startswith("zh") else "English"
    system = (
        "You recommend ComfyUI plugins based only on the supplied live GitHub candidates. "
        "Never invent repositories or claim security/compatibility certainty. Rank 3-5 useful candidates; "
        "for each give purpose, why it matches, cautious installation note, and the supplied URL. "
        f"Answer in {locale}."
    )
    user = "User need: " + query + "\n\nLive GitHub candidates:\n" + json.dumps(candidates, ensure_ascii=False)
    return {"result": _chat(system, user), "candidates": candidates, "query": query}


def hot_topics(days: int = 7, language: str = "zh", query: str = "") -> dict[str, Any]:
    """Build a short, source-grounded ComfyUI/AI digest or keyword search."""
    days = 30 if int(days) >= 30 else 7
    query = str(query or "").strip()[:300]
    since = (datetime.now(UTC) - timedelta(days=days)).date().isoformat()
    queries = (
        [f"{query} pushed:>={since}"]
        if query
        else [f"topic:comfyui pushed:>={since}", f"ComfyUI model pushed:>={since}"]
    )
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for query in queries:
        for item in _github_candidates(query):
            if item["url"] and item["url"] not in seen:
                seen.add(item["url"])
                candidates.append(item)
    candidates = candidates[:12]
    locale = "Chinese" if language.lower().startswith("zh") else "English"
    system = (
        "You are a careful AI and ComfyUI news editor. Use only the supplied public GitHub candidates. "
        "Do not invent releases, benchmarks, popularity, or URLs. Return ONLY valid JSON, no Markdown and no code fence: "
        '{"brief":"one short sentence", "stories":[{"url":"exact supplied URL", "title":"plain, easy-to-read headline", '
        '"description":"one or two short factual sentences"}]}. '
        "Create at most 10 stories. Each story URL must exactly match a supplied URL. "
        "Titles and descriptions must be concise and easy to scan in a browser results list. "
        f"Write all natural-language text in {locale}."
    )
    user = (
        f"Period: recent {days} days, since {since}. Search intent: {query or 'default recent AI and ComfyUI activity'}.\n"
        "Public GitHub candidates:\n" + json.dumps(candidates, ensure_ascii=False)
    )
    raw_result = _chat(system, user)
    cleaned = raw_result.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    brief = ""
    story_map: dict[str, dict[str, str]] = {}
    try:
        structured = json.loads(cleaned)
        brief = str(structured.get("brief") or "").strip()[:500]
        for story in structured.get("stories") or []:
            if not isinstance(story, dict):
                continue
            url = str(story.get("url") or "")
            if url:
                story_map[url] = {
                    "title": str(story.get("title") or "").strip()[:180],
                    "description": str(story.get("description") or "").strip()[:700],
                }
    except (TypeError, ValueError):
        # A malformed AI reply must never be dumped as unreadable Markdown in
        # the feed.  The source cards below remain useful without a briefing.
        brief = ""
    enriched = []
    for item in candidates:
        story = story_map.get(item["url"], {})
        enriched.append({
            **item,
            "title": story.get("title") or item["name"],
            "summary": story.get("description") or item["description"],
        })
    return {"result": brief, "candidates": enriched, "days": days, "since": since, "query": query}
