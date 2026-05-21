
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
from pathlib import Path
 
import arxiv  # pip install arxiv>=2.1
 
 
def fetch_new_listings(category: str, max_results: int = 500) -> list[arxiv.Result]:
    """Return arxiv.Result objects submitted on the latest available date.
 
    `max_results` is a safety cap; daily category volume is typically well below
    this. If a category routinely exceeds the cap, raise it or paginate.
    """
    client = arxiv.Client(page_size=100, delay_seconds=3.0, num_retries=3)
    search = arxiv.Search(
        query=f"cat:{category}",
        max_results=max_results,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending,
    )
    results = list(client.results(search))
    if not results:
        return []
 
    latest = results[0].published.date()  # most recent submission date in batch
    return [r for r in results if r.published.date() == latest]
 
 
def write_csv(papers: list[arxiv.Result], path: Path) -> None:
    fields = [
        "arxiv_id", "published_utc", "updated_utc", "title",
        "authors", "primary_category", "categories",
        "abstract", "pdf_url", "entry_url",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for p in papers:
            writer.writerow({
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
 
 
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("category", nargs="?", default="hep-th",
                    help="arXiv category, e.g. hep-th, astro-ph.CO, cond-mat.str-el, cs.LG")
    ap.add_argument("--max", type=int, default=500, help="result cap (safety)")
    ap.add_argument("--out", type=Path, default=None, help="output CSV path")
    args = ap.parse_args()
 
    papers = fetch_new_listings(args.category, args.max)
    if not papers:
        print(f"No results for category {args.category!r}.")
        return
 
    latest = papers[0].published.date().isoformat()
    out = args.out or Path(f"arxiv_{args.category.replace('.', '_')}_{latest}.csv")
    write_csv(papers, out)
    print(f"Wrote {len(papers)} entries submitted on {latest} to {out}")
 
 
if __name__ == "__main__":
    main()