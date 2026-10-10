"""Citation verification: do the PMIDs and links in a consult note exist and fit the claim?

Language models invent references. Every PMID a note cites is looked up in PubMed (esummary) and
labelled `verified` (exists; the cited label/claim shares meaningful words with the real title),
`mismatch` (exists, but about something else — likely a wrong or invented ID), `not_found`, or
`unchecked` (PubMed unreachable — never reported as verified). URLs are not fetched (no SSRF
surface); they are labelled by whether the domain is a recognised guideline/journal publisher.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from .llm.anthropic_engine import GUIDELINE_DOMAINS

MAX_PMIDS = 25
_STOP = set(
    "a an and are as at be by for from in into is of on or the to with without vs versus among after before "
    "study trial review guideline guidelines patients patient adults children clinical randomized meta analysis "
    "systematic effect effects outcomes treatment management".split()
)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in _STOP}


def _context_for(pmid: str, markdown: str, citations: list[dict[str, Any]]) -> str:
    parts = [f"{c.get('label', '')} {c.get('supports', '')}" for c in citations if c.get("pmid") == pmid]
    for line in markdown.splitlines():
        if pmid in line:
            parts.append(line)
    return " ".join(parts)


def trusted_domain(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in GUIDELINE_DOMAINS)


async def verify(markdown: str, structured: dict[str, Any], summaries=None) -> dict[str, Any]:
    """`summaries(pmids) -> {pmid: {"title": ...}}` is injectable for tests; defaults to PubMed."""
    from .consult_note import merge_reported_pmids
    from .sources import pubmed_summaries

    summaries = summaries or pubmed_summaries
    citations = structured.get("citations", [])
    pmids = merge_reported_pmids(structured, markdown)[:MAX_PMIDS]
    results: list[dict[str, Any]] = []
    looked_up: dict[str, Any] | None
    try:
        looked_up = await summaries(pmids) if pmids else {}
    except Exception as exc:  # network/upstream failure is "unchecked", never a pass
        looked_up, error = None, f"{type(exc).__name__}: {exc}"
    for pmid in pmids:
        if looked_up is None:
            results.append({"pmid": pmid, "status": "unchecked", "reason": error})
            continue
        art = looked_up.get(pmid)
        if not art or not art.get("title"):
            results.append({"pmid": pmid, "status": "not_found"})
            continue
        claim = _words(_context_for(pmid, markdown, citations)) - {pmid}
        overlap = claim & _words(art["title"])
        status = "verified" if overlap or not claim else "mismatch"
        results.append({"pmid": pmid, "status": status, "title": art["title"], "pubdate": art.get("pubdate"),
                        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"})
    urls = list(dict.fromkeys([c["url"] for c in citations if c.get("url")] + re.findall(r"https?://[^\s)>\]]+", markdown)))
    links = [{"url": u, "trusted_publisher": trusted_domain(u)} for u in urls[:MAX_PMIDS]]
    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"pmids": results, "links": links, "counts": counts,
            "all_verified": bool(results) and counts.get("verified", 0) == len(results)}
