"""Normalize keyword proposals and group them by intent and destination."""

INTENTS = {"commercial", "transactional", "informational", "local"}

def cluster(rows):
    clusters = {}
    for row in rows:
        term = " ".join(row["term"].split())
        if not term or row["intent"] not in INTENTS or not row["page"]:
            raise ValueError("Keyword needs a term, intent and destination")
        key = (row["intent"], row["page"])
        clusters.setdefault(key, {}).setdefault(term.casefold(), term)
    return [{"intent": intent, "page": page, "terms": list(terms.values())}
            for (intent, page), terms in clusters.items()]
