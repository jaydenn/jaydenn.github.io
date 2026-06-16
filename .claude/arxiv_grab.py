"""
Fetch the current day's new arXiv submissions for given categories via RSS
and write them to a scored CSV.

arXiv publishes one RSS 2.0 feed per category at:
    https://rss.arxiv.org/rss/{category}

Each feed contains today's announcement batch (new submissions + cross-lists).
RSS has no rate limits and always reflects the current day.

No external dependencies — stdlib only.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import email.utils
import html as html_mod
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

# Only the prefixed elements carry namespaces; core RSS 2.0 elements do not.
_DC = "http://purl.org/dc/elements/1.1/"
_AX = "http://arxiv.org/schemas/atom"

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


# ── Parsing helpers ──────────────────────────────────────────────────────────

def _txt(el: ET.Element | None, default: str = "") -> str:
    if el is None or not el.text:
        return default
    return el.text.strip()


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(html_mod.unescape(text).split())


def _clean_abstract(desc: str) -> str:
    """Strip the 'arXiv:XXXX Announce Type: ...\nAbstract:' prefix arXiv adds."""
    desc = re.sub(
        r"^arXiv:\S+\s+Announce\s+Type:\s+\S+\s*\n?Abstract:\s*",
        "", desc.strip(), flags=re.IGNORECASE,
    )
    return _strip_html(desc).strip()


def _parse_pubdate(s: str) -> dt.datetime:
    """Parse an RFC 2822 pubDate string into an aware UTC datetime."""
    try:
        return email.utils.parsedate_to_datetime(s).astimezone(dt.timezone.utc)
    except Exception:
        return dt.datetime.now(dt.timezone.utc)


def _arxiv_id_from_url(url: str) -> str:
    m = re.search(r"arxiv\.org/abs/([^\s?#]+)", url)
    return m.group(1) if m else url


# ── RSS fetching ─────────────────────────────────────────────────────────────

def fetch_rss_category(category: str) -> list[dict]:
    """Return today's new + cross-list submissions for one category via RSS."""
    url = f"https://rss.arxiv.org/rss/{category}"
    req = urllib.request.Request(url, headers={"User-Agent": "arxiv-digest/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not fetch RSS for {category!r}: {e}") from e

    root = ET.fromstring(raw)
    channel = root.find("channel")
    if channel is None:
        return []

    papers = []
    for item in channel.findall("item"):
        # Skip replacements; keep new submissions and cross-lists
        announce = _txt(item.find(f"{{{_AX}}}announce_type"), "new")
        if announce in ("replace", "replace-cross"):
            continue

        link     = _txt(item.find("link"))
        arxiv_id = _arxiv_id_from_url(link)
        title    = _txt(item.find("title"))
        abstract = _clean_abstract(_txt(item.find("description")))
        authors  = _txt(item.find(f"{{{_DC}}}creator"))
        pub_date = _txt(item.find("pubDate"))

        cats    = [c.text.strip() for c in item.findall("category") if c.text]
        primary = cats[0] if cats else category

        pub_dt = _parse_pubdate(pub_date) if pub_date else dt.datetime.now(dt.timezone.utc)

        papers.append({
            "arxiv_id":         arxiv_id,
            "published_utc":    pub_dt.isoformat(),
            "updated_utc":      pub_dt.isoformat(),
            "title":            title,
            "authors":          authors,
            "primary_category": primary,
            "categories":       "; ".join(cats),
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
