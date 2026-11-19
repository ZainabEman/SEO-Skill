"""Score explicit, evidence-backed relevance dimensions."""
WEIGHTS = {"services": 35, "audience": 20, "geography": 15, "language": 10,
           "model": 10, "scale": 5, "keywords": 5}

def score(signals):
    if set(signals) - WEIGHTS.keys():
        raise ValueError("Unknown relevance dimension")
    for signal in signals.values():
        if signal["match"] not in (0, 0.5, 1) or not signal["evidence"]:
            raise ValueError("Each assessed dimension needs a match and evidence")
    points = sum(WEIGHTS[key] * signal["match"] for key, signal in signals.items())
    coverage = sum(WEIGHTS[key] for key in signals)
    direct = points >= 55 and signals.get("services", {}).get("match", 0) > 0
    return {"points": points, "coverage": coverage, "direct": direct,
            "confidence": "high" if coverage >= 85 else "medium" if coverage >= 60 else "low"}
