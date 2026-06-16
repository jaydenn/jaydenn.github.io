"""
Fetch the current day's new arXiv submissions for given categories via RSS
and write them to a scored CSV.

arXiv publishes one RSS feed per category at:
    https://rss.arxiv.org/rss/{category}

Each feed contains exactly today's announcement batch (new submissions +
cross-lists).  RSS has no rate limits and always reflects the current day,
unlike the search API which requires pagination, has rate limits, and can
return stale ordering.

Dependencies: feedparser  (pip install feedparser)
The 'arxiv' package is no longer required.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html as html_mod
import re
from pathlib import Path

import feedparser  # pip install feedparser

TIER_SCORES = {1: 4, 2: 3, 3: 2, 4: 1}
DEFAULT_KEYWORDS_PATH = Path(__file__).parent / "jay_keywords.txt"
DEFAULT_CATEGORIES = ["hep-th", "hep-ph", "hep-ex", "astro-ph", "nucl-th"]


# ── Keyword loading & scoring ────────────────────────────────────────────────

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
            continue
        if current_tier is not None:
            phrases = [p.strip().lower() for p in line.split(";") if p.strip()]
            keywords.setdefault(current_tier, []).extend(phrases)
    return keywords


def score_paper(title: str, abstract: str, keywords: dict[int, list[str]]) -> int:
    """Sum tier points for every keyword phrase found in title or abstract."""
    text = (title + " " + abstract).lower()
    total = 0
    for tier, phrases in keywords.items():
        points = TIER_SCORES.get(tier, 0)
        for phrase in phrases:
            if phrase in text:
                total += points
    return total


# ── RSS helpers ──────────────────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(html_mod.unescape(text).split())


def _arxiv_id_from_url(url: str) -> str:
    m = re.search(r"arxiv\.org/abs/([^\s?#]+)", url)
    return m.group(1) if m else url


def fetch_rss_category(category: str) -> list[dict]:
    """Return today's new + cross-list submissions for one category via RSS."""
    url = f"https://rss.arxiv.org/rss/{category}"
    feed = feedparser.parse(url)

    if feed.bozo and not feed.entries:
        raise RuntimeError(
            f"Could not parse RSS for {category!r}: {feed.bozo_exception}"
        )

    papers = []
    for entry in feed.entries:
        # Skip replacements — only want new submissions and cross-lists
        announce = getattr(entry, "arxiv_announce_type", "new")
        if announce in ("replace", "replace-cross"):
            continue

        link = entry.get("link", "")
        arxiv_id = _arxiv_id_from_url(link)

        # Titles in the feed often have "(arXiv:XXXX.XXXXXvN [cat])" appended
        title = re.sub(r"\s*\(arXiv:\S+\)\s*$", "", entry.get("title", "")).strip()

        abstract = _strip_html(entry.get("summary", ""))

        # Authors: feedparser exposes dc:creator as entry.author (semicolon-sep string)
        # or as a list via entry.authors depending on feed format
        if hasattr(entry, "authors") and entry.authors:
            authors = "; ".join(a.get("name", "") for a in entry.authors)
        else:
            authors = entry.get("author", "")

        # Categories from <category> / <tags>
        tags = [t.get("term", "") for t in entry.get("tags", [])]
        primary = tags[0] if tags else category

        pub = entry.get("published_parsed") or entry.get("updated_parsed")
        pub_dt = (
            dt.datetime(*pub[:6], tzinfo=dt.timezone.utc)
            if pub
            else dt.datetime.now(dt.timezone.utc)
        )

        papers.append({
            "arxiv_id":         arxiv_id,
            "published_utc":    pub_dt.isoformat(),
            "updated_utc":      pub_dt.isoformat(),
            "title":            title,
            "authors":          authors,
            "primary_category": primary,
            "categories":       "; ".join(tags),
            "abstract":         abstract,
            "pdf_url":          f"https://arxiv.org/pdf/{arxiv_id}",
            "entry_url":        link,
        })
    return papers


def fetch_rss_multi(categories: list[str]) -> list[dict]:
    """Fetch RSS for multiple categories, deduplicating by arXiv ID."""
    seen: set[str] = set()
    combined: list[dict] = []
    for cat in categories:
        print(f"Fetching {cat}...")
        papers = fetch_rss_category(cat)
        added = 0
        for p in papers:
            if p["arxiv_id"] not in seen:
                seen.add(p["arxiv_id"])
                combined.append(p)
                added += 1
        print(f"  {added} new  ({len(papers)} in feed, {len(papers) - added} duplicates)")
    return combined


# ── CSV output ───────────────────────────────────────────────────────────────

def write_csv(papers: list[dict], path: Path, keywords: dict[int, list[str]]) -> None:
    fields = [
        "score", "arxiv_id", "published_utc", "updated_utc", "title",
        "authors", "primary_category", "categories",
        "abstract", "pdf_url", "entry_url",
    ]
    scored = sorted(
        [(score_paper(p["title"], p["abstract"], keywords), p) for p in papers],
        key=lambda x: x[0],
        reverse=True,
    )
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for score, p in scored:
            writer.writerow({"score": score, **p})


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "category", nargs="*", default=DEFAULT_CATEGORIES,
        help="arXiv categories to fetch (default: hep-th hep-ph hep-ex astro-ph nucl-th)",
    )
    ap.add_argument("--out", type=Path, default=None, help="output CSV path")
    ap.add_argument(
        "--keywords", type=Path, default=DEFAULT_KEYWORDS_PATH,
        help="keyword tier file for scoring (default: jay_keywords.txt)",
    )
    args = ap.parse_args()

    keywords = load_keywords(args.keywords)
    print(
        f"Loaded {sum(len(v) for v in keywords.values())} keyword phrases "
        f"across {len(keywords)} tiers"
    )

    papers = fetch_rss_multi(args.category)
    if not papers:
        print("No papers found.")
        return

    today = dt.date.today().isoformat()
    cats_slug = "_".join(c.replace(".", "_") for c in args.category)
    out = args.out or Path(f"arxiv_{cats_slug}_{today}.csv")
    write_csv(papers, out, keywords)
    print(f"Wrote {len(papers)} unique papers to {out}")


if __name__ == "__main__":
    main()
