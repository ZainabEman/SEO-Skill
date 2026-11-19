"""Validate structured claims and resolve strict RFC 6901 references."""
import argparse
import re
from urllib.parse import urlsplit

from run_state import read
from pathlib import Path

TAGS = {"M", "F", "E", "A", "V"}
SHEETS = ["Keywords", "Keyword Gaps", "Keyword Clusters", "Direct Competitors", "SERP Competitors",
          "Technical Issues", "Backlinks", "Content Opportunities", "Prioritized Actions", "Sources", "Cost Ledger"]
NUMBER = re.compile(r"(?<![\w/-])[-+]?\d+(?:[.,]\d+)?(?:\s*[%€$])?(?![\w/-])")

def pointer(document, path):
    if not isinstance(path, str) or path and not path.startswith("/"):
        raise ValueError("Invalid JSON pointer")
    if not path:
        return document
    for segment in path[1:].split("/"):
        if re.search(r"~(?![01])", segment):
            raise ValueError("Invalid JSON pointer escape")
        token = segment.replace("~1", "/").replace("~0", "~")
        try:
            if isinstance(document, list) and re.fullmatch(r"0|[1-9][0-9]*", token):
                document = document[int(token)]
            elif isinstance(document, dict):
                document = document[token]
            else:
                raise KeyError(token)
        except (KeyError, IndexError) as exc:
            raise ValueError(f"Unresolved JSON pointer: {path}") from exc
    return document

def validate(report, sources):
    if not all(report.get(key) for key in ("client", "website", "market", "date", "language")):
        raise ValueError("Report metadata incomplete")
    if set(report["tables"]) != set(SHEETS):
        raise ValueError("Required workbook tables missing")
    if len(report["sections"]) != 23:
        raise ValueError("Report requires 23 sections")
    if report["tables"]["Sources"]["columns"] != ["ID", "URL", "Datum", "Markt", "Anbieter", "Grenzen"]:
        raise ValueError("Sources table schema mismatch")
    source_index = {}
    for row in report["tables"]["Sources"]["rows"]:
        record = dict(zip(report["tables"]["Sources"]["columns"], row, strict=True))
        if record["ID"] in source_index or not all(record.values()):
            raise ValueError("Duplicate or incomplete source")
        if urlsplit(record["URL"]).scheme not in {"http", "https"} or not urlsplit(record["URL"]).hostname:
            raise ValueError("Invalid source URL")
        source_index[record["ID"]] = record
    claim_ids = set()
    pending_dependencies = set()
    for section in report["sections"]:
        if not section["title"] or not section["claims"]:
            raise ValueError("Section has no content")
        for claim in section["claims"]:
            tag = claim["tag"]
            if tag not in TAGS or not claim["text"].strip() or claim["id"] in claim_ids:
                raise ValueError("Invalid or duplicate claim")
            claim_ids.add(claim["id"])
            pending_dependencies.update(claim.get("findings", []))
            if claim["id"] in claim.get("findings", []):
                raise ValueError("A claim cannot support itself")
            refs = claim.get("evidence", [])
            if tag in {"M", "F", "E"} and not refs:
                raise ValueError("Observed claims require evidence")
            if tag == "A" and not refs and not claim.get("findings"):
                raise ValueError("Interpretations require a source or finding")
            values = []
            for ref in refs:
                if ref["source"] not in source_index or ref["source"] not in sources:
                    raise ValueError("Unknown evidence source")
                if not ref["pointer"]:
                    raise ValueError("Reference a specific source field")
                value = pointer(sources[ref["source"]], ref["pointer"])
                if type(value) in (int, float):
                    values.append(str(value))
            for match in NUMBER.findall(claim["text"]):
                if match.replace(",", ".").rstrip(" %€$") not in values:
                    raise ValueError(f"Unverified report number: {match}")
    if not pending_dependencies <= claim_ids:
        raise ValueError("Unknown supporting finding")
    for table in report["tables"].values():
        metric_evidence = table.get("metric_evidence", {})
        columns = table["columns"]
        if not columns or len(columns) != len(set(columns)):
            raise ValueError("Invalid workbook columns")
        for index, row in enumerate(table["rows"]):
            if len(row) != len(columns) or any(type(cell) not in (str, int, float, bool, type(None)) for cell in row):
                raise ValueError("Invalid workbook row")
            for column, cell in zip(columns, row):
                if column == "Beleg" and cell and cell not in source_index and cell not in claim_ids:
                    raise ValueError("Unknown workbook source")
                if type(cell) in (int, float):
                    info = metric_evidence.get(str(index), {}).get(column)
                    if not info or info["tag"] not in {"M", "F", "E"} or not info["unit"]:
                        raise ValueError("Numeric workbook cells require provenance and units")
                    if info["source"] not in source_index:
                        raise ValueError("Unknown numeric workbook source")
                    value = pointer(sources[info["source"]], info["pointer"])
                    if type(value) not in (int, float) or value != cell:
                        raise ValueError("Workbook metric disagrees with source")

if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("report", type=Path)
    cli.add_argument("sources", type=Path)
    opts = cli.parse_args()
    validate(read(opts.report), read(opts.sources))
