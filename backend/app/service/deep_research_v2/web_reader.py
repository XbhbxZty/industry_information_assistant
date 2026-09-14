"""Bounded public HTML reader; pins validated DNS addresses, follows safe redirects.

Only called for URLs returned by an enabled search tool. No cookies, credentials,
environment proxies, JavaScript execution or access to private network addresses.
"""
import ipaddress
import socket
from html.parser import HTMLParser
from urllib.parse import urlsplit, urljoin

import urllib3


def public_target(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("仅支持无凭据的 HTTP(S) 公共网页")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in (80, 443):
        raise ValueError("网页端口不受支持")
    addresses = {row[4][0] for row in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError("网页地址不是公网地址（也可能是代理 Fake-IP），无法读取正文")
    return parsed, port, sorted(addresses)[0]


class TextReader(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "noscript", "svg"):
            self.hidden += 1
        if tag in ("p", "div", "br", "tr", "li", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "noscript", "svg"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def read_public_page(url):
    for _ in range(4):
        parsed, port, ip = public_target(url)
        common = {"port": port, "timeout": urllib3.Timeout(connect=5, read=10)}
        if parsed.scheme == "https":
            pool = urllib3.HTTPSConnectionPool(ip, server_hostname=parsed.hostname,
                                             assert_hostname=parsed.hostname, cert_reqs="CERT_REQUIRED", **common)
        else:
            pool = urllib3.HTTPConnectionPool(ip, **common)
        response = None
        try:
            path = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
            response = pool.request("GET", path, headers={"Host": parsed.netloc, "Accept-Encoding": "identity",
                                   "User-Agent": "DueDiligenceResearch/1.0"},
                                   preload_content=False, redirect=False, retries=False)
            if response.status in (301, 302, 303, 307, 308):
                url = urljoin(url, response.headers.get("Location", ""))
                continue
            if response.status != 200:
                raise ValueError(f"正文读取失败：HTTP {response.status}")
            content_type = response.headers.get("Content-Type", "").lower()
            if "text/html" not in content_type and "text/plain" not in content_type:
                raise ValueError("该来源不是 HTML/文本网页，请使用上传材料或其他来源")
            raw = response.read(512_001, decode_content=False)
            if len(raw) > 512_000:
                raise ValueError("网页超过读取大小上限")
            charset = content_type.split("charset=")[-1].split(";")[0].strip(' "') if "charset=" in content_type else "utf-8"
            text = raw.decode(charset, errors="replace")
            if "text/html" in content_type:
                parser = TextReader()
                parser.feed(text)
                text = "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())
            if not text.strip():
                raise ValueError("网页未返回可读正文")
            return {"text": text[:18000], "url": url, "truncated": len(text) > 18000}
        finally:
            if response is not None:
                response.close()
            pool.close()
    raise ValueError("网页重定向次数超过上限")
