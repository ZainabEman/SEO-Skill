"""Bounded public HTML collection with robots rules checked on every page hop."""
import ipaddress
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request
from urllib.robotparser import RobotFileParser

from lxml import html
from defusedxml.ElementTree import fromstring

AGENT = "ChatGPT-User"
REDIRECT = {301, 302, 303, 307, 308}
MAX_BYTES = 2_000_000

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def normalize(value):
    value = value.strip()
    p = urlsplit(value if "://" in value else "https://" + value)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise ValueError("Expected a public HTTP(S) URL without credentials")
    if p.port is not None and p.port != {"http": 80, "https": 443}[p.scheme]:
        raise ValueError("Only the standard HTTP(S) ports are supported")
    host = p.hostname.encode("idna").decode().lower()
    if "." not in host or not re.fullmatch(r"[a-z0-9.-]+", host):
        raise ValueError("Expected a public DNS name")
    return urlunsplit((p.scheme, host, p.path or "/", p.query, ""))

def site(url):
    return urlsplit(url).hostname.removeprefix("www.")

def public(url):
    for item in socket.getaddrinfo(urlsplit(url).hostname, None, type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(item[4][0]).is_global:
            raise ValueError("Destination does not resolve exclusively to public addresses")

def download(url):
    public(url)
    req = Request(url, headers={"User-Agent": AGENT, "Accept-Encoding": "identity"})
    try:
        response = build_opener(NoRedirect()).open(req, timeout=8)
    except HTTPError as error:
        response = error
    with response:
        body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("Response exceeds size limit")
        return response.code, dict((k.lower(), v) for k, v in response.headers.items()), body

class Crawler:
    def __init__(self, transport=download):
        self.transport = transport
        self.robots = {}

    def fetch(self, url, respect=True):
        chain = []
        current = url
        allowed_host = site(url)
        try:
            for _ in range(6):
                if respect and not self.rules(current)["parser"].can_fetch(AGENT, current):
                    raise ValueError("Blocked by robots policy")
                status, headers, body = self.transport(current)
                chain.append({"url": current, "status": status})
                if status not in REDIRECT or "location" not in headers:
                    return current, chain, headers, body, None
                current = normalize(urljoin(current, headers["location"]))
                if site(current) != allowed_host:
                    raise ValueError("Cross-domain redirect requires a new explicit target")
            raise ValueError("Redirect limit exceeded")
        except (ValueError, OSError, URLError) as exc:
            return current, chain, {}, b"", str(exc)

    def rules(self, url):
        root = urljoin(url, "/robots.txt")
        if root not in self.robots:
            final, chain, _, body, error = self.fetch(root, respect=False)
            status = chain[-1]["status"] if chain else None
            parser = RobotFileParser(root)
            if not error and status == 200:
                parser.parse(body.decode("utf-8", "replace").splitlines())
            elif not error and status in {404, 410}:
                parser.allow_all = True
            else:
                parser.disallow_all = True
            self.robots[root] = {"parser": parser, "url": final, "chain": chain,
                                 "sitemaps": parser.site_maps() or [], "error": error}
        return self.robots[root]

    def sitemaps(self, start, ceiling):
        queue = list(self.rules(start)["sitemaps"]) or [urljoin(start, "/sitemap.xml")]
        seen = set()
        pages = {}
        records = []
        while queue and len(seen) < 8 and len(pages) < ceiling:
            try:
                address = normalize(queue.pop(0))
            except ValueError:
                continue
            if site(address) != site(start) or address in seen:
                continue
            seen.add(address)
            final, chain, _, body, error = self.fetch(address)
            locations = []
            if not error and chain[-1]["status"] == 200:
                try:
                    document = fromstring(body)
                    kind = document.tag.rsplit("}", 1)[-1]
                    if kind not in {"sitemapindex", "urlset"}:
                        raise ValueError("Unsupported sitemap root")
                    locations = [node.text.strip() for node in document.iter()
                                 if node.tag.rsplit("}", 1)[-1] == "loc" and node.text]
                    if kind == "sitemapindex":
                        queue.extend(locations[:100])
                    else:
                        for location in locations:
                            if len(pages) >= ceiling:
                                break
                            try:
                                candidate = normalize(location)
                            except ValueError:
                                continue
                            if site(candidate) == site(start):
                                pages[candidate] = None
                except Exception as exc:
                    error = f"Sitemap parsing failed ({type(exc).__name__})"
            records.append({"url": final, "chain": chain, "locations": locations[:ceiling], "error": error})
        return list(pages), records

    def page(self, address):
        final, chain, headers, body, error = self.fetch(address)
        record = {"url": final, "chain": chain, "error": error,
                  "status": chain[-1]["status"] if chain else None,
                  "x_robots": headers.get("x-robots-tag")}
        if error or record["status"] != 200 or "html" not in headers.get("content-type", ""):
            return record
        try:
            document = html.fromstring(body)
        except (ValueError, html.etree.ParserError):
            record["error"] = "Empty or unreadable HTML"
            return record
        for node in document.xpath("//script[not(@type='application/ld+json')]|//style|//noscript"):
            node.drop_tree()
        clean = lambda s: " ".join(s.split())
        links = []
        for node in document.xpath("//a[@href]"):
            try:
                raw = node.get("href")
                if not raw or raw.startswith(("mailto:", "tel:", "#", "javascript:")):
                    continue
                destination = normalize(urljoin(final, raw))
            except ValueError:
                continue
            links.append({"url": destination, "href": raw, "text": clean(node.text_content())[:180]})
        record.update({
            "lang": document.get("lang"),
            "title": clean(document.xpath("string(//title)")),
            "description": document.xpath("//meta[translate(@name,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='description']/@content"),
            "robots": document.xpath("//meta[@name='robots' or @name='googlebot']/@content"),
            "canonical": document.xpath("//link[contains(concat(' ',normalize-space(@rel),' '),' canonical ')]/@href"),
            "alternates": [[node.get("hreflang"), node.get("href")]
                           for node in document.xpath("//link[@hreflang and @href]")],
            "headings": [[node.tag, clean(node.text_content())] for node in document.xpath("//h1|//h2|//h3|//h4|//h5|//h6")][:100],
            "images": {"total": len(document.xpath("//img")), "missing_alt": len(document.xpath("//img[not(@alt)]"))},
            "schema": len(document.xpath("//script[@type='application/ld+json']")),
            "links": links[:300],
            "text": clean(" ".join(document.xpath("//body//text()[not(ancestor::script)]")))[:10000],
        })
        return record

    def crawl(self, start, limit, content_limit=None):
        homepage = self.page(start)
        sitemap_urls, sitemaps = self.sitemaps(homepage["url"], limit * 8)
        links = [link["url"] for link in homepage.get("links", [])]
        candidates = dict.fromkeys([*links, *sitemap_urls])
        queue = sorted(candidates, key=lambda url: urlsplit(url).path.strip("/").count("/"))
        pages = [homepage]
        for address in queue:
            if len(pages) >= limit:
                break
            if address in {start, homepage["url"]} or site(address) != site(start):
                continue
            if urlsplit(address).query or re.search(r"\.(pdf|jpe?g|png|webp|zip|xml|mp4)$", urlsplit(address).path, re.I):
                continue
            page = self.page(address)
            if content_limit is not None and len(pages) >= content_limit:
                page.pop("text", None)
            pages.append(page)
        return {"pages": pages, "robots": [{k: v for k, v in record.items() if k != "parser"}
                                          for record in self.robots.values()], "sitemaps": sitemaps}
