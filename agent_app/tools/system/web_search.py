from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from ..context import ToolContext
from ..parsers import SearchResultParser

TOOL_NAME = "web_search"
TOOL_ORDER = 60
REQUIRES_CONTAINER = False
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Search the web for current information such as docs, APIs, product specs, prices, weather, or library usage. Search results are leads; use original/reliable sources before final claims when accuracy matters.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Web search query."},
            },
            "required": ["query"],
        },
    },
}

BLOCKED_HOSTS = {
    "lmstudio.co.com",
    "www.lmstudio.co.com",
}


def run(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args["query"]).strip()
    if not query:
        return {"ok": False, "error": "query is required and cannot be empty."}
    with httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": "ThursdayAgent/1.0"}) as client:
        response = client.get("https://lite.duckduckgo.com/lite/", params={"q": query})
        response.raise_for_status()

    parser = SearchResultParser()
    parser.feed(response.text)
    results = [result for result in parser.results if not blocked_result(result)]
    return {
        "ok": True,
        "query": query,
        "results": results,
        "count": len(results),
        "source": "DuckDuckGo Lite",
    }


def blocked_result(result: dict[str, str]) -> bool:
    host = urlparse(str(result.get("url") or "")).netloc.lower()
    return host in BLOCKED_HOSTS
