"""Generate evidence-backed issue candidates from sampled HTML."""
import re
from urllib.parse import urljoin, urlsplit

def audit(pages, language):
    problems = []
    for index, page in enumerate(pages):
        base = f"/pages/{index}"
        if page["status"] != 200 or page["error"]:
            problems.append((base, "retrieval"))
        if "title" not in page:
            continue
        if not page["title"]:
            problems.append((base + "/title", "title_missing"))
        if not page["description"]:
            problems.append((base + "/description", "description_missing"))
        if (page["lang"] or "").split("-")[0].lower() != language:
            problems.append((base + "/lang", "language_review"))
        if sum(tag == "h1" for tag, _ in page["headings"]) != 1:
            problems.append((base + "/headings", "h1_review"))
        canonical = page["canonical"]
        if not canonical:
            problems.append((base + "/canonical", "canonical_missing"))
        elif len(canonical) != 1 or urljoin(page["url"], canonical[0]) != page["url"]:
            problems.append((base + "/canonical", "canonical_review"))
        signals = [("/robots", " ".join(page.get("robots", []))),
                   ("/x_robots", page.get("x_robots") or "")]
        for field, text in signals:
            if re.search(r"(?:^|[\s,:])(?:noindex|none)(?:$|[\s,])", text, re.I):
                problems.append((base + field, "indexability_review"))
        if any(not code or urlsplit(urljoin(page["url"], url)).scheme not in {"https", "http"}
               for code, url in page.get("alternates", [])):
            problems.append((base + "/alternates", "alternate_review"))
        if page["images"]["missing_alt"]:
            problems.append((base + "/images/missing_alt", "alt_missing"))
    return problems
