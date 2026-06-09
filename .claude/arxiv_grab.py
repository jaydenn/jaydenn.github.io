
"""
Fetch the most recent day's arXiv submissions for a given category and write
them to CSV.
 
Approximates the website's "new" listing by querying the arXiv API sorted by
submittedDate (descending) and keeping every entry whose submission date equals
the most recent date returned.
 
API reference: https://info.arxiv.org/help/api/user-manual.html
Python wrapper: https://github.com/lukasschwab/arxiv.py  (pip install arxiv)
"""
 
from __future__ import annotations
 
import argparse
import csv
import datetime as dt
import re
import time
from pathlib import Path

import arxiv  # pip install arxiv>=2.1

TIER_SCORES = {1: 4, 2: 3, 3: 2, 4: 1}
DEFAULT_KEYWORDS_PATH = Path(__file__).parent / "jay_keywords.txt"


def load_keywords(path: Path) -> dict[int, list[str]]:
    """Parse jay_keywords.txt into {tier: [keyword, ...]}."""
    tier_re = re.compile(r"Tier\s+(\d+)", re.IGNORECASE)
    keywords: dict[int, list[str]] = {}
    current_tier: int | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        m = tier_re.search(line)
        if m:
            current_tier = int(m.group(1))
            continue  # header line, not keywords
        if current_tier is not None:
            phrases = [p.strip().lower() for p in line.split(";") if p.strip()]
            keywords.setdefault(current_tier, []).extend(phrases)
    return keywords


def score_paper(title: str, abstract: str, keywords: dict[int, list[str]]) -> int:
    """Sum tier points for every keyword phrase found in the title or abstract (case-insensitive)."""
    text = (title + " " + abstract).lower()
    total = 0
    for tier, phrases in keywords.items():
        points = TIER_SCORES.get(tier, 0)
        for phrase in phrases:
            if phrase in text:
                total += points
    return total


def fetch_new_listings(category: str, max_results: int = 500) -> list[arxiv.Result]:
    """Return arxiv.Result objects submitted on the latest available date.

    `max_results` is a safety cap; daily category volume is typically well below
    this. If a category routinely exceeds the cap, raise it or paginate.
    """
    client = arxiv.Client(page_size=100, delay_seconds=5.0, num_retries=1)
    search = arxiv.Search(
        query=f"cat:{category}",
        max_results=max_results,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending,
    )
    results = None
    for wait in [30, 60, 120]:
        try:
            results = list(client.results(search))
            break
        except arxiv.HTTPError as e:
            if e.status != 429:
                raise
            print(f"  429 rate-limited, retrying in {wait}s...")
            time.sleep(wait)
    if results is None:
        raise RuntimeError(f"arXiv API rate-limited after all retries for category {category!r}")
    if not results:
        return []

    latest = results[0].published.date()  # most recent submission date in batch
    return [r for r in results if r.published.date() == latest]


def fetch_new_listings_multi(categories: list[str], max_results: int = 500) -> list[arxiv.Result]:
    """Fetch new listings across multiple categories, deduplicating by arxiv ID."""
    seen: set[str] = set()
    combined: list[arxiv.Result] = []
    for cat in categories:
        print(f"Fetching {cat}...")
        papers = fetch_new_listings(cat, max_results)
        print(f"  {len(papers)} new papers in {cat}")
        for p in papers:
            pid = p.get_short_id()
            if pid not in seen:
                seen.add(pid)
                combined.append(p)
    return combined
 
 
def write_csv(papers: list[arxiv.Result], path: Path, keywords: dict[int, list[str]]) -> None:
    fields = [
        "score", "arxiv_id", "published_utc", "updated_utc", "title",
        "authors", "primary_category", "categories",
        "abstract", "pdf_url", "entry_url",
    ]
    scored = sorted(
        [(score_paper(p.title, p.summary, keywords), p) for p in papers],
        key=lambda x: x[0],
        reverse=True,
    )
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for score, p in scored:
            writer.writerow({
                "score":            score,
                "arxiv_id":         p.get_short_id(),
                "published_utc":    p.published.astimezone(dt.timezone.utc).isoformat(),
                "updated_utc":      p.updated.astimezone(dt.timezone.utc).isoformat(),
                "title":            " ".join(p.title.split()),
                "authors":          "; ".join(a.name for a in p.authors),
                "primary_category": p.primary_category,
                "categories":       "; ".join(p.categories),
                "abstract":         " ".join(p.summary.split()),
                "pdf_url":          p.pdf_url,
                "entry_url":        p.entry_id,
            })
 
 
DEFAULT_CATEGORIES = ["hep-th", "hep-ph", "hep-ex"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("category", nargs="*", default=DEFAULT_CATEGORIES,
                    help="arXiv categories to fetch (default: hep-th hep-ph hep-ex)")
    ap.add_argument("--max", type=int, default=500, help="result cap per category (safety)")
    ap.add_argument("--out", type=Path, default=None, help="output CSV path")
    ap.add_argument("--keywords", type=Path, default=DEFAULT_KEYWORDS_PATH,
                    help="keyword tier file for scoring (default: jay_keywords.txt)")
    args = ap.parse_args()

    keywords = load_keywords(args.keywords)
    print(f"Loaded keywords: {sum(len(v) for v in keywords.values())} phrases across {len(keywords)} tiers")

    papers = fetch_new_listings_multi(args.category, args.max)
    if not papers:
        print(f"No results for categories: {', '.join(args.category)}")
        return

    latest = papers[0].published.date().isoformat()
    cats_slug = "_".join(c.replace(".", "_") for c in args.category)
    out = args.out or Path(f"arxiv_{cats_slug}_{latest}.csv")
    write_csv(papers, out, keywords)
    print(f"Wrote {len(papers)} unique entries submitted on {latest} to {out}")
 
 
if __name__ == "__main__":
    main()