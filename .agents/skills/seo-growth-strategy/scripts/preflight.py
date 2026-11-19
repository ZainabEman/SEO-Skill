"""Skill entry point for a bounded website evidence run."""
import argparse
from pathlib import Path
import re
import tomllib

from fetch_pages import Crawler, normalize, site
from run_state import ROOT, create, read, write

def market(value):
    if not re.fullmatch(r"[A-Z]{2}/[a-z]{2,3}", value):
        raise argparse.ArgumentTypeError("Use market format DE/de")
    return value

def collect(run):
    state = read(run / "state.json")
    cfg = tomllib.loads((ROOT / "config/defaults.toml").read_text())
    params = state["parameters"]
    profile = cfg["profiles"][params["profile"]]
    targets = [params["url"], *params["competitors"]]
    sites = {}
    for index, url in enumerate(targets):
        limit = profile["pages"] if index == 0 else profile["competitor_pages"]
        sites[site(url)] = Crawler().crawl(url, limit, profile["content"] if index == 0 else limit)
        write(run / "website.json", sites)
    state["stage"] = "website_collected"
    write(run / "state.json", state)

def parse():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", nargs="?", type=normalize)
    parser.add_argument("--market", type=market)
    parser.add_argument("--profile", choices=("lite", "full"), default="lite")
    parser.add_argument("--language")
    parser.add_argument("--competitor", action="append", type=normalize, default=[])
    parser.add_argument("--pdf", action="store_true")
    parser.add_argument("--base", type=Path, default=Path(".runs"))
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    if not args.resume and (not args.url or not args.market):
        parser.error("URL and market are required for a new run")
    return args

def main():
    args = parse()
    if args.resume:
        run = args.resume.resolve()
        if args.refresh or read(run / "state.json").get("stage") != "website_collected":
            collect(run)
    else:
        competitors = {}
        for url in args.competitor:
            if site(url) != site(args.url):
                competitors.setdefault(site(url), url)
        if len(competitors) > (3 if args.profile == "lite" else 5):
            raise SystemExit("Competitor list exceeds the selected profile ceiling")
        run = create(args.base, {"url": args.url, "market": args.market, "profile": args.profile,
                                "language": args.language or args.market.split("/")[1],
                                "competitors": list(competitors.values()), "pdf": args.pdf})
        print(run.resolve(), flush=True)
        collect(run)

if __name__ == "__main__":
    main()
