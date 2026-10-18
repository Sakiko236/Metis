from __future__ import annotations
import asyncio
import html
import re
import ssl
import urllib.parse
import urllib.request
from typing import List

from models import SearchResultItem


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

LINK_RE = re.compile(r"""<a\b[^>]*\bclass=['"][^'"]*result[_-]?(?:link|a)['"][^>]*>([\s\S]*?)</a>""", re.IGNORECASE)
SNIPPET_RE = re.compile(
    r"""<(?:td|a|div)\b[^>]*class=['"][^'"]*result[_-]snippet['"][^>]*>([\s\S]*?)</(?:td|a|div)>""",
    re.IGNORECASE,
)
HREF_RE = re.compile(r"""href=['"]([^'"]+)['"]""", re.IGNORECASE)
UDDG_RE = re.compile(r"""uddg=([^&'"]+)""", re.IGNORECASE)
TAG_RE = re.compile(r"""<[^>]+>""")


class WebSearchRunner:
    @classmethod
    async def search(cls, query: str, max_results: int = 5) -> List[SearchResultItem]:
        q = query.strip()
        if not q:
            return []
        return await asyncio.to_thread(cls._search_sync, q, max_results)

    @classmethod
    def _search_sync(cls, query: str, max_results: int) -> List[SearchResultItem]:
        for fetcher in (cls._try_duckduckgo_lite, cls._try_duckduckgo_html):
            try:
                res = fetcher(query, max_results)
                if res:
                    return res
            except Exception:
                continue
        return []

    @classmethod
    def _try_duckduckgo_lite(cls, query: str, max_results: int) -> List[SearchResultItem]:
        data = urllib.parse.urlencode({"q": query}).encode("utf-8")
        req = urllib.request.Request(
            "https://lite.duckduckgo.com/lite/",
            data=data,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=12, context=ctx) as resp:
            raw_html = resp.read().decode("utf-8", errors="replace")
        return cls._parse_html(raw_html, max_results)

    @classmethod
    def _try_duckduckgo_html(cls, query: str, max_results: int) -> List[SearchResultItem]:
        params = urllib.parse.urlencode({"q": query})
        url = f"https://html.duckduckgo.com/html/?{params}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT},
            method="GET",
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=12, context=ctx) as resp:
            raw_html = resp.read().decode("utf-8", errors="replace")
        return cls._parse_html(raw_html, max_results)

    @classmethod
    def _parse_html(cls, raw_html: str, max_results: int) -> List[SearchResultItem]:
        link_matches = list(LINK_RE.finditer(raw_html))
        snippet_matches = list(SNIPPET_RE.finditer(raw_html))

        results: List[SearchResultItem] = []
        limit = min(max_results, len(link_matches))

        for i in range(limit):
            m = link_matches[i]
            whole_tag = m.group(0)
            title_raw = m.group(1)

            link = ""
            h_cap = HREF_RE.search(whole_tag)
            if h_cap:
                raw_href = h_cap.group(1)
                u_cap = UDDG_RE.search(raw_href)
                if u_cap:
                    link = urllib.parse.unquote(u_cap.group(1))
                else:
                    link = raw_href

            title = cls._clean_html(title_raw)
            snippet = cls._clean_html(snippet_matches[i].group(1)) if i < len(snippet_matches) else ""

            if title or link:
                results.append(SearchResultItem(title=title, url=link, snippet=snippet))

        return results

    @staticmethod
    def _clean_html(text: str) -> str:
        stripped = TAG_RE.sub("", text)
        unescaped = html.unescape(stripped)
        return " ".join(unescaped.split())

    @staticmethod
    def format_markdown(query: str, results: List[SearchResultItem]) -> str:
        lines = [f"### 🔍 Autonomous Web Search Results for: `{query}`\n\n"]
        if not results:
            lines.append("No relevant search results found. Try refining keywords.\n")
            return "".join(lines)

        for i, r in enumerate(results, start=1):
            t = r.title if r.title else "Web Result"
            lines.append(f"{i}. **[{t}]({r.url})**\n   {r.snippet}\n\n")
        return "".join(lines)
