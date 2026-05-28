from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse


class SearchResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._href = ""
        self._text: list[str] = []
        self._in_link = False
        self._seen_urls: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        if not href:
            return
        self._href = href
        self._text = []
        self._in_link = True

    def handle_data(self, data: str) -> None:
        if self._in_link:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._in_link:
            return
        title = " ".join("".join(self._text).split())
        url = self._normalize_url(self._href)
        self._href = ""
        self._text = []
        self._in_link = False
        if not title or not url or url in self._seen_urls:
            return
        self._seen_urls.add(url)
        self.results.append({"title": title, "url": url})

    def _normalize_url(self, href: str) -> str:
        if href.startswith("//"):
            href = "https:" + href
        parsed = urlparse(href)
        query = parse_qs(parsed.query)
        if "uddg" in query and query["uddg"]:
            return unquote(query["uddg"][0])
        if parsed.scheme in {"http", "https"} and "duckduckgo.com" not in parsed.netloc:
            return href
        return ""


class PageSummaryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._hidden_depth = 0
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered == "title":
            self._in_title = True
        if lowered in {"script", "style", "noscript", "svg", "head"}:
            self._hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "title":
            self._in_title = False
        if lowered in {"script", "style", "noscript", "svg", "head"} and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        if self._in_title and not self.title:
            self.title = text
            return
        if not self._hidden_depth:
            self._text_parts.append(text)

    def visible_text(self) -> str:
        return "\n".join(self._text_parts)
