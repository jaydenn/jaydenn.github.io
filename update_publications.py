#!/usr/bin/env python3
"""
Fetch the N most recent papers for a given INSPIRE author and update the
pub-list section of index.html to match the site's existing formatting.

Usage:
    python update_publications.py            # fetches 5 papers (default)
    python update_publications.py 8          # fetches 8 papers
"""

import re
import sys
import html as html_lib
import requests

INSPIRE_BAI = "Jayden.L.Newstead.1"
INDEX_HTML = "index.html"
HIGHLIGHT_NAME = "Newstead"  # last name to bold in author list


def fetch_papers(n: int = 5) -> list:
    url = "https://inspirehep.net/api/literature"
    params = {
        "sort": "mostrecent",
        "size": n,
        "q": f"a {INSPIRE_BAI}",
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    return r.json()["hits"]["hits"]


def abbreviate_name(full_name: str) -> str:
    """Convert 'Last, First Middle' to 'F. M. Last'."""
    if "," not in full_name:
        return full_name
    last, _, rest = full_name.partition(",")
    initials = " ".join(p[0] + "." for p in rest.split() if p)
    return f"{initials} {last.strip()}"


def format_authors(authors: list) -> str:
    names = []
    for a in authors:
        name = abbreviate_name(a.get("full_name", ""))
        if HIGHLIGHT_NAME.lower() in a.get("full_name", "").lower():
            name = f"<strong>{html_lib.escape(name, quote=False)}</strong>"
        else:
            name = html_lib.escape(name, quote=False)
        names.append(name)

    if len(names) > 7:
        names = names[:7] + ["et al."]
    return ", ".join(names)


def format_paper(paper: dict) -> str:
    meta = paper.get("metadata", {})

    # Title
    titles = meta.get("titles", [])
    title = html_lib.escape(titles[0]["title"]) if titles else "[No title]"

    # Authors
    authors_html = format_authors(meta.get("authors", []))

    # Journal / year
    pub_infos = meta.get("publication_info", [])
    journal_html = ""
    year = ""
    for p in pub_infos:
        journal = p.get("journal_title", "")
        volume  = p.get("journal_volume", "")
        page    = p.get("page_start", "") or p.get("artid", "")
        year    = str(p.get("year", ""))
        if journal:
            parts = [f"<em>{html_lib.escape(journal)}</em>"]
            if volume:
                parts.append(f"<strong>{html_lib.escape(volume)}</strong>")
            if page:
                parts.append(html_lib.escape(page))
            if year:
                parts.append(f"({year})")
            journal_html = " ".join(parts)
            break  # use first published entry

    if not year:
        preprint = meta.get("preprint_date", "")
        if preprint:
            year = preprint[:4]

    # arXiv
    eprints = meta.get("arxiv_eprints", [])
    arxiv_html = ""
    if eprints:
        arxiv_id = eprints[0]["value"]
        arxiv_html = (
            f'<a href="https://arxiv.org/abs/{arxiv_id}">arXiv:{arxiv_id}</a>'
        )

    # DOI
    dois = meta.get("dois", [])
    doi_html = ""
    if dois:
        doi = dois[0]["value"]
        doi_html = f'<a href="https://doi.org/{doi}">DOI</a>'

    # Build meta line
    meta_parts = [authors_html]
    if journal_html:
        meta_parts.append(f"&mdash;\n                    {journal_html}")
    elif year:
        meta_parts.append(f"({year})")

    links = " &nbsp;·&nbsp; ".join(filter(None, [doi_html, arxiv_html]))
    if links:
        meta_parts.append(f"&nbsp;·&nbsp; {links}")

    meta_line = "\n                    ".join(meta_parts)

    return (
        f'            <li>\n'
        f'                <div class="pub-title">&ldquo;{title}&rdquo;</div>\n'
        f'                <div class="pub-meta">\n'
        f'                    {meta_line}\n'
        f'                </div>\n'
        f'            </li>'
    )


def update_html(papers: list) -> None:
    with open(INDEX_HTML, "r", encoding="utf-8") as f:
        content = f.read()

    items = "\n".join(format_paper(p) for p in papers)
    new_ul = f'<ul class="pub-list">\n{items}\n        </ul>'

    updated, count = re.subn(
        r'<ul class="pub-list">.*?</ul>',
        new_ul,
        content,
        flags=re.DOTALL,
    )

    if count == 0:
        print("ERROR: could not find <ul class=\"pub-list\"> in index.html")
        sys.exit(1)

    with open(INDEX_HTML, "w", encoding="utf-8") as f:
        f.write(updated)

    print(f"Updated {INDEX_HTML} with {len(papers)} papers.")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    print(f"Fetching {n} most recent papers from INSPIRE …")
    papers = fetch_papers(n)
    print(f"  Got {len(papers)} papers.")
    update_html(papers)
