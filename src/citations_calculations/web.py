"""Bounded public HTTP acquisition with DNS/IP pinning and checks at every redirect."""

import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit, urljoin

from .extract import public_uri, extract, MAX_BYTES
from . import __version__
from .store import Store, EvidenceError, now


def addresses(host, port):
    candidates = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    ips = list(dict.fromkeys(row[4][0] for row in candidates))
    if not ips or any(not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast for ip in ips):
        raise EvidenceError("Web fetch is limited to public Internet addresses")
    return ips


class PinnedConnection(http.client.HTTPConnection):
    def __init__(self, host, port, ip, tls):
        super().__init__(host, port, timeout=20)
        self.ip = ip
        self.tls = tls

    def connect(self):
        sock = socket.create_connection((self.ip, self.port), timeout=self.timeout)
        try:
            self.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host) if self.tls else sock
        except Exception:
            sock.close()
            raise


def fetch(root, url, title=None):
    initial = public_uri(url)
    current = initial
    redirects = []
    for step in range(6):
        p = urlsplit(public_uri(current))
        port = p.port or (443 if p.scheme == "https" else 80)
        if port not in (80, 443):
            raise EvidenceError("Public web acquisition uses only ports 80 and 443")
        ip = addresses(p.hostname, port)[0]
        connection = PinnedConnection(p.hostname, port, ip, p.scheme == "https")
        try:
            connection.request(
                "GET",
                (p.path or "/") + ("?" + p.query if p.query else ""),
                headers={
                    "User-Agent": f"citations-and-calculations/{__version__} (public evidence capture)",
                    "Accept": "text/html,application/pdf,application/json,text/plain,text/csv",
                    "Accept-Encoding": "identity",
                },
            )
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                target = response.getheader("Location")
                if not target:
                    raise EvidenceError("Redirect has no location")
                target = urljoin(current, target)
                public_uri(target)
                if p.scheme == "https" and urlsplit(target).scheme != "https":
                    raise EvidenceError("HTTPS redirect cannot downgrade to HTTP")
                redirects.append({"from": current, "to": target, "status": response.status})
                current = target
                continue
            if response.status != 200:
                raise EvidenceError(f"Web source unavailable: HTTP {response.status}")
            declared = response.getheader("Content-Length")
            if declared and int(declared) > MAX_BYTES:
                raise EvidenceError("Web response exceeds 20 MiB")
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise EvidenceError("Web response exceeds 20 MiB")
            header = response.getheader("Content-Type") or ""
            content_type = header.split(";")[0].strip().lower()
            charset = next(
                (
                    p.split("=", 1)[1].strip().strip("\"'")
                    for p in header.split(";")[1:]
                    if p.strip().lower().startswith("charset=")
                ),
                None,
            )
            format = {
                "text/html": "html",
                "application/pdf": "pdf",
                "application/json": "json",
                "text/plain": "txt",
                "text/csv": "csv",
            }.get(content_type)
            if not format:
                raise EvidenceError("Unsupported web content type: " + content_type)
            extraction = extract(raw, format, charset)
            provenance = {
                "title": title or p.hostname,
                "origin": "web_fetch",
                "uri": current,
                "requested_url": initial,
                "redirects": redirects,
                "http_status": 200,
                "content_type": content_type,
                "acquired_at": now(),
                "published_at": None,
                "completeness": "complete",
                "authenticity": "Direct public HTTP response captured by the script; no authentication, model extraction or publication-date inference",
            }
            sid = Store(root).add(raw, extraction, provenance)
            return Store(root).search(sid)
        finally:
            connection.close()
    raise EvidenceError("Too many redirects (maximum 5)")
