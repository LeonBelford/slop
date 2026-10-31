"""Read-only web access for the agent: fetch pages and (optionally) search.

Safety: only http/https, no private/loopback addresses (also checked on every redirect), size and time limits,
text extraction only. Content is returned as plain text and must be treated as untrusted by callers.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

UA = "Mozilla/5.0 (compatible; saas-agent/1.0; +local research)"
MAX_BYTES = 1_500_000
TEXT_TYPES = ("text/html", "text/plain", "text/markdown", "application/json", "application/xml", "text/xml",
              "application/xhtml+xml", "application/rss+xml", "application/atom+xml")


class WebError(Exception):
    pass


def check_url(url: str, blocked: list[str] | None = None) -> None:
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise WebError("only http(s) URLs are allowed")
    host = u.hostname.lower()
    if any(host == b or host.endswith("." + b) for b in (blocked or [])):
        raise WebError(f"domain blocked in config: {host}")
    try:
        infos = socket.getaddrinfo(host, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise WebError(f"cannot resolve {host}: {e}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise WebError(f"refusing to access non-public address {ip} ({host})")


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "pre", "section", "article"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    p = _Text()
    try:
        p.feed(html)
    except Exception:  # noqa: BLE001 - malformed HTML should still yield text
        pass
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.parts))
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return re.sub(r"\s+", " ", p.title).strip(), text


class _Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, blocked):
        self.blocked = blocked

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl, self.blocked)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Web:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.mock_fetch = None   # tests: callable(url) -> (title, text)
        self.mock_search = None  # tests: callable(query) -> [(url, title)]

    @property
    def blocked(self) -> list[str]:
        return [d.lower() for d in self.cfg.get("blocked_domains", [])]

    def fetch(self, url: str) -> tuple[str, str]:
        """Returns (title, plain text) of a page, clipped to cfg['max_chars']."""
        if self.mock_fetch:
            title, text = self.mock_fetch(url)
        else:
            check_url(url, self.blocked)
            opener = urllib.request.build_opener(_Redirects(self.blocked))
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,text/plain,*/*;q=0.5"})
            try:
                with opener.open(req, timeout=self.cfg.get("timeout", 20)) as r:
                    ctype = (r.headers.get_content_type() or "").lower()
                    if ctype not in TEXT_TYPES:
                        raise WebError(f"unsupported content type {ctype}")
                    raw = r.read(MAX_BYTES)
                    charset = r.headers.get_content_charset() or "utf-8"
            except urllib.error.HTTPError as e:
                raise WebError(f"HTTP {e.code}") from e
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                raise WebError(str(getattr(e, "reason", e))) from e
            body = raw.decode(charset, "replace")
            title, text = html_to_text(body) if "html" in ctype else ("", body.strip())
        limit = int(self.cfg.get("max_chars", 20000))
        return title, (text if len(text) <= limit else text[:limit] + "\n...[truncated]")

    def search(self, query: str) -> list[tuple[str, str]]:
        """Returns [(url, title)] from the configured search endpoint (DuckDuckGo HTML by default, or SearXNG with format=json)."""
        if self.mock_search:
            return self.mock_search(query)
        tpl = self.cfg.get("search_url") or "https://html.duckduckgo.com/html/?q={q}"
        url = tpl.replace("{q}", urllib.parse.quote_plus(query))
        check_url(url, self.blocked)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.get("timeout", 20)) as r:
                body = r.read(MAX_BYTES).decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise WebError(f"search failed: {getattr(e, 'reason', e)}") from e
        out: list[tuple[str, str]] = []
        if "format=json" in tpl:
            for r in json.loads(body).get("results", []):
                out.append((r.get("url", ""), r.get("title", "")))
        else:
            for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', body, re.S):
                href, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
                if "uddg=" in href:
                    href = urllib.parse.unquote(href.split("uddg=", 1)[1].split("&", 1)[0])
                out.append((href, title.strip()))
        return [(u, t) for u, t in out if u.startswith(("http://", "https://"))]
