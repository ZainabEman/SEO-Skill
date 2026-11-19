"""Provider cache and an injectable boundary for a verified metered transport."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from cost_ledger import fingerprint
from run_state import now, read, write

def cache_key(tool, parameters, account):
    return fingerprint({"provider": "OpenSEO", "tool": tool, "parameters": parameters, "account": account})

def lookup(directory, key, ttl=30):
    path = directory / (key + ".json")
    if not path.exists():
        return None
    record = read(path)
    if datetime.now(timezone.utc) - datetime.fromisoformat(record["captured"]) > timedelta(days=ttl):
        return None
    return record

def save(directory, key, result):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    write(directory / (key + ".json"), {"captured": now(), "result": result})

def invoke_metered(transport, ledger, request, approval, balance):
    """Transport must provide provider-authoritative charge totals or leave pending."""
    request = deepcopy(request)
    reservation = ledger.reserve(request, approval, balance)
    result, actual_usd, actual_credits = transport(request["tool"], request["parameters"])
    outcome = ledger.reconcile(reservation, actual_usd, actual_credits)
    return result, outcome
