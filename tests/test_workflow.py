"""Offline tests with injected website and provider responses."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agents/skills/seo-growth-strategy/scripts"))

from competitor_scoring import score
from cost_ledger import Balance, Ledger, amount, plan
from fetch_pages import Crawler, normalize
from keyword_processing import cluster
from preflight import market
from provider_adapter import cache_key, invoke_metered, lookup, save
from render_report import render
from run_state import create, now, read
from technical_seo import audit
from validate_report import SHEETS, pointer, validate

PAGE = b"<html lang='de'><title>Beratung</title><body><h1>Angebot</h1><a href='/frage'>Kontakt</a><img src='x'>"
HOME = "https://example.com/"

class PublicResearch(unittest.TestCase):
    def crawler(self, replies):
        def fetch(url):
            if url.endswith("/robots.txt"):
                return replies.get(url, (404, {}, b""))
            return replies[url]
        return Crawler(fetch)

    def test_normalize(self):
        self.assertEqual(normalize("EXAMPLE.COM/test#x"), HOME + "test")

    def test_invalid_target(self):
        for data in ("file:///tmp/a", "https://u:p@example.com", "https://localhost", "http://example.com:81"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                normalize(data)

    def test_market(self):
        self.assertEqual(market("DE/de"), "DE/de")
        with self.assertRaises(ValueError):
            normalize("https://example.com:bad")

    def test_malformed_html(self):
        page = self.crawler({HOME: (200, {"content-type": "text/html"}, PAGE)}).page(HOME)
        self.assertEqual(page["lang"], "de")
        self.assertEqual(page["images"]["missing_alt"], 1)
        self.assertEqual(page["links"][0]["href"], "/frage")

    def test_same_site_redirect(self):
        page = self.crawler({HOME: (301, {"location": "/de/"}, b""),
                            HOME+"de/": (200, {"content-type": "text/html"}, PAGE)}).page(HOME)
        self.assertEqual([p["status"] for p in page["chain"]], [301, 200])

    def test_cross_site_redirect(self):
        page = self.crawler({HOME: (301, {"location": "https://elsewhere.com"}, b"")}).page(HOME)
        self.assertIn("Cross-domain", page["error"])

    def test_robots_restriction(self):
        page = self.crawler({HOME+"robots.txt": (200, {}, b"User-agent: *\nDisallow: /frage")}).page(HOME+"frage")
        self.assertIn("robots", page["error"])

    def test_robots_service_unavailable(self):
        page = self.crawler({HOME+"robots.txt": (503, {}, b"")}).page(HOME)
        self.assertIsNone(page["status"])

    def test_missing_sitemap(self):
        crawler = self.crawler({HOME+"sitemap.xml": (404, {}, b"")})
        pages, records = crawler.sitemaps(HOME, 10)
        self.assertEqual(pages, [])
        self.assertEqual(records[0]["chain"][0]["status"], 404)

    def test_sitemap_limit(self):
        body = "<urlset>" + "".join(f"<url><loc>{HOME}{i}</loc></url>" for i in range(100)) + "</urlset>"
        pages, _ = self.crawler({HOME+"sitemap.xml": (200, {}, body.encode())}).sitemaps(HOME, 5)
        self.assertEqual(len(pages), 5)

    def test_empty_html(self):
        page = self.crawler({HOME: (200, {"content-type": "text/html"}, b"")}).page(HOME)
        self.assertIn("Empty", page["error"])

    def test_unreachable(self):
        def unavailable(_):
            raise TimeoutError("timed out")
        result = Crawler(unavailable).fetch(HOME, respect=False)
        self.assertEqual(result[-1], "timed out")

    def test_technical_canonical_and_noindex(self):
        headers = {"content-type": "text/html", "x-robots-tag": "googlebot: noindex"}
        body = PAGE.replace(b"<title>", b"<link rel='canonical' href='/alternate'><title>")
        page = self.crawler({HOME: (200, headers, body)}).page(HOME)
        issues = audit([page], "de")
        self.assertIn(("/pages/0/canonical", "canonical_review"), issues)
        self.assertIn(("/pages/0/x_robots", "indexability_review"), issues)

    def test_unique_state(self):
        with TemporaryDirectory() as temp:
            base = Path(temp)
            first = create(base, {"market": "DE/de"})
            second = create(base, {})
            self.assertNotEqual(first, second)
            self.assertEqual(read(first/"state.json")["parameters"]["market"], "DE/de")

    def test_scoring_missing_data(self):
        self.assertFalse(score({"services":{"match":1,"evidence":["source"]}})["direct"])
        self.assertEqual(score({})["coverage"], 0)

    def test_scoring_evidence_required(self):
        with self.assertRaises(ValueError):
            score({"services":{"match":1,"evidence":[]}})

    def test_cluster_deduplication(self):
        rows = [{"term": term,"intent":"commercial","page":"/offer"} for term in ["KI Beratung","ki   beratung"]]
        self.assertEqual(cluster(rows)[0]["terms"], ["KI Beratung"])

class CostControls(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.ledger = Ledger(self.path/"book.sqlite")
        self.balance = Balance(500, now())

    def request(self, run="run", max_usd="2.50", credits=250):
        r = plan(run, "provider_fixture", {"market":"DE/de"}, max_usd,
                 "fixture: exact maximum", credits, "account")
        return r, {"fingerprint":r["fingerprint"],"approved_by":"tester","usd":max_usd,"credits":credits}

    def test_no_approval(self):
        request, _ = self.request()
        with self.assertRaises(ValueError):
            self.ledger.reserve(request, None, self.balance)

    def test_unknown_cost(self):
        with self.assertRaises(ValueError):
            self.request(max_usd=None)

    def test_changed_parameters(self):
        request, approval = self.request()
        request["parameters"]["market"] = "US/en"
        with self.assertRaises(ValueError):
            self.ledger.reserve(request, approval, self.balance)

    def test_below_bound(self):
        request, approval = self.request()
        approval["usd"] = "0.10"
        with self.assertRaises(ValueError):
            self.ledger.reserve(request, approval, self.balance)

    def test_insufficient_balance(self):
        request, approval = self.request()
        with self.assertRaises(ValueError):
            self.ledger.reserve(request, approval, Balance(200, now()))

    def test_stale_balance(self):
        request, approval = self.request()
        previous = datetime.now(timezone.utc) - timedelta(minutes=6)
        with self.assertRaises(ValueError):
            self.ledger.reserve(request, approval, Balance(500, previous.isoformat()))

    def test_concurrent_balance(self):
        def reserve(run_id):
            request, approval = self.request(run_id, credits=300)
            try:
                self.ledger.reserve(request, approval, self.balance)
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(reserve, ["a","b"])), [False,True])

    def test_settled_call_against_same_balance_snapshot(self):
        one, ok = self.request("a", credits=300)
        key = self.ledger.reserve(one, ok, self.balance)
        self.ledger.reconcile(key, "1.00", 300)
        two, ok = self.request("b", credits=300)
        with self.assertRaisesRegex(ValueError, "balance already reserved"):
            self.ledger.reserve(two, ok, self.balance)

    def test_duplicate(self):
        request, approval = self.request()
        self.ledger.reserve(request, approval, self.balance)
        with self.assertRaisesRegex(ValueError, "already reserved"):
            self.ledger.reserve(request, approval, self.balance)

    def test_interrupted_request_is_pending(self):
        request, approval = self.request()
        def timeout(*_):
            raise TimeoutError("fixture timeout")
        with self.assertRaises(TimeoutError):
            invoke_metered(timeout, self.ledger, request, approval, self.balance)
        with self.assertRaises(ValueError):
            invoke_metered(timeout, self.ledger, request, approval, self.balance)
        self.assertEqual(self.ledger.reconcile(request["fingerprint"], "0", 0), "settled")

    def test_monthly_limit(self):
        for i in range(5):
            request, approval = self.request(str(i), "5.00", 0)
            key = self.ledger.reserve(request, approval, self.balance)
            self.ledger.reconcile(key, "5.00", 0)
        request, approval = self.request("six", "0.01", 0)
        with self.assertRaisesRegex(ValueError, "ceiling"):
            self.ledger.reserve(request, approval, self.balance)

    def test_per_run_limit(self):
        request, approval = self.request(max_usd="5.01")
        with self.assertRaisesRegex(ValueError, "ceiling"):
            self.ledger.reserve(request, approval, self.balance)

    def test_precision(self):
        self.assertEqual(amount("0.30") - amount("0.20"), amount("0.10"))
        with self.assertRaises(ValueError):
            amount("0.0000001")

    def test_overage_blocks_next_dispatch(self):
        request, approval = self.request()
        key = self.ledger.reserve(request, approval, self.balance)
        self.assertEqual(self.ledger.reconcile(key, "2.51", 250), "overage")
        other, ok = self.request("x", credits=1)
        with self.assertRaisesRegex(ValueError, "administrator review"):
            self.ledger.reserve(other, ok, self.balance)

    def test_cache(self):
        key = cache_key("fixture", {"market":"DE/de"}, "account")
        save(self.path, key, {"partial":[]})
        self.assertEqual(lookup(self.path, key)["result"], {"partial":[]})
        self.assertIsNone(lookup(self.path, key, ttl=-1))
        self.assertNotEqual(key, cache_key("fixture", {"market":"DE/de"}, "other"))

class Reports(unittest.TestCase):
    def example(self):
        sources = {"X":{"response":{"count":3}}}
        tables = {name:{"columns":["Status"],"rows":[]} for name in SHEETS}
        tables["Keywords"]["rows"] = [["=unsafe formula"]]
        tables["Sources"] = {"columns":["ID","URL","Datum","Markt","Anbieter","Grenzen"],
                             "rows":[["X",HOME,"2026-11-19","DE/de","fixture","offline"]]}
        sections = [{"title":f"Section {i}","claims":[{"id":str(i),"tag":"M","text":"Count is 3",
                     "evidence":[{"source":"X","pointer":"/response/count"}]}]} for i in range(23)]
        return {"client":"Fixture","website":HOME,"date":"2026-11-19","market":"DE/de",
                "language":"en","sections":sections,"tables":tables}, sources

    def test_pointer_escapes(self):
        self.assertEqual(pointer({"a/b":{"~c":[0]}}, "/a~1b/~0c/0"), 0)

    def test_nonexistent_pointer(self):
        report, sources = self.example()
        report["sections"][0]["claims"][0]["evidence"][0]["pointer"] = "/response/missing"
        with self.assertRaisesRegex(ValueError, "Unresolved"):
            validate(report, sources)

    def test_invalid_pointer_subscripts(self):
        for path in ("/-1", "/01", "/~2"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                pointer([7, 8], path)

    def test_unsupported_numeric_claim(self):
        report, sources = self.example()
        report["sections"][0]["claims"][0]["text"] = "Count is 35"
        with self.assertRaisesRegex(ValueError, "Unverified"):
            validate(report, sources)

    def test_unsupported_numeric_workbook_cell(self):
        report, sources = self.example()
        report["tables"]["Keywords"]["rows"] = [[90]]
        with self.assertRaisesRegex(ValueError, "require provenance"):
            validate(report, sources)

    def test_native_round_trip(self):
        from docx import Document
        from openpyxl import load_workbook
        report, sources = self.example()
        with TemporaryDirectory() as temp:
            dest = Path(temp)
            render(report, sources, dest)
            wb = load_workbook(dest/"data.xlsx")
            self.assertEqual(wb["Keywords"]["A2"].data_type, "s")
            self.assertEqual(wb["Keywords"].auto_filter.ref, "A1:A2")
            self.assertTrue(wb["Sources"]["B2"].hyperlink)
            self.assertTrue(Document(dest/"report.docx").paragraphs[2].hyperlinks)
            wb.close()

if __name__ == "__main__":
    unittest.main()
