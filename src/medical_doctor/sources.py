"""Authoritative, free, keyless medical data sources.

  * openFDA drug labels (FDA-approved prescribing information)
  * NLM RxNorm (drug name normalization)
  * NCBI PubMed E-utilities (literature)
  * ClinicalTrials.gov API v2 (trials)

Set NCBI_API_KEY to raise PubMed rate limits and OPENFDA_API_KEY for openFDA.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from contextlib import asynccontextmanager
from typing import Any

import anyio
import httpx2 as httpx

OPENFDA = "https://api.fda.gov"
RXNAV = "https://rxnav.nlm.nih.gov/REST"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
CTGOV = "https://clinicaltrials.gov/api/v2"

TIMEOUT = httpx.Timeout(20.0)
USER_AGENT = "medical-doctor-mcp/0.1 (+https://github.com/sstarzh/medical-doctor)"

LABEL_SECTIONS = (
    "boxed_warning",
    "indications_and_usage",
    "dosage_and_administration",
    "contraindications",
    "warnings_and_cautions",
    "warnings",
    "drug_interactions",
    "use_in_specific_populations",
    "pregnancy",
    "adverse_reactions",
    "overdosage",
    "mechanism_of_action",
)


MAX_INTERACTION_DRUGS = 12
_RETRY_STATUS = {429, 500, 502, 503, 504}

# One pooled client per event loop (connection reuse + TLS session reuse across calls).
_shared: dict[int, httpx.AsyncClient] = {}


@asynccontextmanager
async def _client():
    loop_id = id(asyncio.get_running_loop())
    client = _shared.get(loop_id)
    if client is None or client.is_closed:
        _shared.clear()  # drop clients bound to dead loops (serverless runtimes)
        client = httpx.AsyncClient(
            timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        _shared[loop_id] = client
    yield client


async def _get(c: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
    """GET with bounded retries on rate limiting / transient upstream errors (openFDA, NCBI)."""
    for attempt in range(3):
        r = await c.get(url, **kwargs)
        if r.status_code not in _RETRY_STATUS or attempt == 2:
            return r
        try:
            wait = float(r.headers.get("retry-after", ""))
        except ValueError:
            wait = 0.5 * 2**attempt
        await asyncio.sleep(min(max(wait, 0.2), 3.0))
    return r


class _TTLCache:
    def __init__(self, ttl: float, max_items: int = 512):
        self.ttl, self.max_items = ttl, max_items
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any:
        hit = self._data.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        self._data.pop(key, None)
        return None

    def set(self, key: str, value: Any) -> None:
        if len(self._data) >= self.max_items:
            self._data.pop(next(iter(self._data)))
        self._data[key] = (time.time() + self.ttl, value)


_label_cache = _TTLCache(6 * 3600)
_rxnorm_cache = _TTLCache(24 * 3600)


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _lucene_escape(term: str) -> str:
    return re.sub(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)', r"\\\1", term.strip())


# --- RxNorm --------------------------------------------------------------


async def normalize_drug(name: str) -> dict[str, Any]:
    """Resolve a brand/misspelled drug name to RxNorm ingredient(s)."""
    key = name.strip().lower()
    if (cached := _rxnorm_cache.get(key)) is not None:
        return cached
    result = await _normalize_drug(name)
    if result.get("found"):
        _rxnorm_cache.set(key, result)
    return result


async def _normalize_drug(name: str) -> dict[str, Any]:
    async with _client() as c:
        r = await _get(c, f"{RXNAV}/approximateTerm.json", params={"term": name, "maxEntries": 3})
        r.raise_for_status()
        cands = (r.json().get("approximateGroup") or {}).get("candidate") or []
        if not cands:
            return {"query": name, "found": False}
        rxcui = cands[0]["rxcui"]
        r2 = await _get(c, f"{RXNAV}/rxcui/{rxcui}/related.json", params={"tty": "IN+BN"})
        r2.raise_for_status()
        groups = (r2.json().get("relatedGroup") or {}).get("conceptGroup") or []
        ingredients, brands = [], []
        for g in groups:
            for p in g.get("conceptProperties") or []:
                (ingredients if g.get("tty") == "IN" else brands).append(p["name"])
        return {"query": name, "found": True, "rxcui": rxcui, "ingredients": ingredients, "brands": brands[:10]}


# --- openFDA labels ------------------------------------------------------


async def _fetch_label(c: httpx.AsyncClient, drug: str) -> dict[str, Any] | None:
    key = drug.strip().lower()
    if (cached := _label_cache.get(key)) is not None:
        return cached
    label = await _fetch_label_uncached(c, drug)
    if label:
        _label_cache.set(key, label)
    return label


async def _fetch_label_uncached(c: httpx.AsyncClient, drug: str) -> dict[str, Any] | None:
    q = _lucene_escape(drug)
    params: dict[str, Any] = {
        "search": f'openfda.generic_name:"{q}" OR openfda.brand_name:"{q}" OR openfda.substance_name:"{q}"',
        "limit": 1,
    }
    if key := os.environ.get("OPENFDA_API_KEY"):
        params["api_key"] = key
    r = await _get(c, f"{OPENFDA}/drug/label.json", params=params)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    results = r.json().get("results") or []
    return results[0] if results else None


def _summarize_label(label: dict[str, Any], sections: tuple[str, ...], max_chars: int) -> dict[str, Any]:
    ofda = label.get("openfda") or {}
    out: dict[str, Any] = {
        "brand_name": ofda.get("brand_name", []),
        "generic_name": ofda.get("generic_name", []),
        "manufacturer": ofda.get("manufacturer_name", [])[:1],
        "route": ofda.get("route", []),
        "effective_time": label.get("effective_time"),
        "set_id": label.get("set_id"),
        "dailymed_url": f"https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid={label.get('set_id')}",
        "sections": {},
    }
    for s in sections:
        if label.get(s):
            out["sections"][s] = _clip(" ".join(label[s]), max_chars)
    return out


async def drug_label(drug: str, sections: list[str] | None = None, max_chars_per_section: int = 2500) -> dict[str, Any]:
    secs = tuple(s for s in (sections or LABEL_SECTIONS) if s in LABEL_SECTIONS) or LABEL_SECTIONS
    async with _client() as c:
        label = await _fetch_label(c, drug)
    if not label:
        return {"drug": drug, "found": False, "hint": "Try the generic name, or call normalize_drug first."}
    return {"drug": drug, "found": True, "source": "FDA Structured Product Label via openFDA", **_summarize_label(label, secs, max_chars_per_section)}


async def drug_interactions(drugs: list[str]) -> dict[str, Any]:
    """Pairwise interaction screen using each FDA label's Drug Interactions section.

    For every pair (A, B) we report the sentences in A's label that mention B
    (or B's class names, when the label lists them), and vice versa.
    """
    drugs = list(dict.fromkeys(d.strip() for d in drugs if d.strip()))
    if len(drugs) < 2:
        raise ValueError("Provide at least two drugs")
    if len(drugs) > MAX_INTERACTION_DRUGS:
        raise ValueError(f"At most {MAX_INTERACTION_DRUGS} drugs per check; split longer lists")
    sem = asyncio.Semaphore(4)

    async def fetch(c: httpx.AsyncClient, d: str) -> dict[str, Any] | None:
        async with sem:
            return await _fetch_label(c, d)

    async with _client() as c:
        fetched = await asyncio.gather(*(fetch(c, d) for d in drugs))
    labels = dict(zip(drugs, fetched))
    return await anyio.to_thread.run_sync(_match_interactions, drugs, labels)


def _match_interactions(drugs: list[str], labels: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    """CPU-bound pairwise text matching; runs in a worker thread."""
    texts: dict[str, str] = {}
    aliases: dict[str, set[str]] = {}
    for d, lab in labels.items():
        names = {d.lower()}
        if lab:
            texts[d] = " ".join(lab.get("drug_interactions") or lab.get("warnings_and_cautions") or [])
            ofda = lab.get("openfda") or {}
            for k in ("generic_name", "brand_name", "substance_name", "pharm_class_epc"):
                for n in ofda.get(k, []):
                    n = re.sub(r"\s*\[EPC\]", "", n).lower()
                    if n:
                        names.add(n)
        aliases[d] = names
    findings = []
    for a in drugs:
        sentences = re.split(r"(?<=[.!?])\s+", texts.get(a, ""))
        for b in drugs:
            if a == b:
                continue
            hits = [s for s in sentences if any(re.search(rf"\b{re.escape(n)}\b", s, re.I) for n in aliases[b])]
            if hits:
                findings.append({"label_of": a, "mentions": b, "excerpts": [_clip(h, 600) for h in hits[:5]]})
    return {
        "drugs": drugs,
        "labels_found": {d: bool(lab) for d, lab in labels.items()},
        "interactions": findings,
        "note": "Absence of a finding is NOT proof of safety: labels are incomplete and class effects "
        "(e.g. serotonergic, QT-prolonging, CYP3A4) may not name every drug. Verify with a pharmacist "
        "or a licensed interaction database (Lexicomp, Micromedex).",
    }


# --- PubMed --------------------------------------------------------------


async def pubmed_search(query: str, max_results: int = 8, reviews_and_trials_only: bool = False, since_year: int | None = None) -> dict[str, Any]:
    term = query
    if reviews_and_trials_only:
        term += " AND (systematic review[pt] OR meta-analysis[pt] OR randomized controlled trial[pt] OR practice guideline[pt] OR guideline[pt])"
    if since_year:
        term += f" AND {since_year}:3000[dp]"
    base: dict[str, Any] = {"db": "pubmed", "retmode": "json"}
    if key := os.environ.get("NCBI_API_KEY"):
        base["api_key"] = key
    async with _client() as c:
        r = await _get(c, f"{EUTILS}/esearch.fcgi", params={**base, "term": term, "retmax": max(1, min(max_results, 25)), "sort": "relevance"})
        r.raise_for_status()
        ids = r.json()["esearchresult"].get("idlist", [])
        if not ids:
            return {"query": term, "count": 0, "articles": []}
        r2 = await _get(c, f"{EUTILS}/esummary.fcgi", params={**base, "id": ",".join(ids)})
        r2.raise_for_status()
        summ = r2.json().get("result", {})
    articles = []
    for pmid in ids:
        a = summ.get(pmid) or {}
        articles.append(
            {
                "pmid": pmid,
                "title": a.get("title"),
                "journal": a.get("fulljournalname") or a.get("source"),
                "pubdate": a.get("pubdate"),
                "authors": [x.get("name") for x in (a.get("authors") or [])[:3]],
                "publication_types": a.get("pubtype", []),
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            }
        )
    return {"query": term, "count": len(articles), "articles": articles}


async def pubmed_abstract(pmid: str) -> dict[str, Any]:
    if not pmid.isdigit():
        raise ValueError("pmid must be numeric")
    params: dict[str, Any] = {"db": "pubmed", "id": pmid, "rettype": "abstract", "retmode": "text"}
    if key := os.environ.get("NCBI_API_KEY"):
        params["api_key"] = key
    async with _client() as c:
        r = await _get(c, f"{EUTILS}/efetch.fcgi", params=params)
        r.raise_for_status()
    return {"pmid": pmid, "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", "abstract": _clip(r.text, 8000)}


# --- ClinicalTrials.gov --------------------------------------------------


async def clinical_trials(condition: str, intervention: str | None = None, location: str | None = None, recruiting_only: bool = True, max_results: int = 10) -> dict[str, Any]:
    params: dict[str, Any] = {"query.cond": condition, "pageSize": max(1, min(max_results, 50)), "format": "json"}
    if intervention:
        params["query.intr"] = intervention
    if location:
        params["query.locn"] = location
    if recruiting_only:
        params["filter.overallStatus"] = "RECRUITING"
    async with _client() as c:
        r = await _get(c, f"{CTGOV}/studies", params=params)
        r.raise_for_status()
        data = r.json()
    studies = []
    for s in data.get("studies", []):
        ps = s.get("protocolSection", {})
        ident = ps.get("identificationModule", {})
        status = ps.get("statusModule", {})
        design = ps.get("designModule", {})
        nct = ident.get("nctId")
        studies.append(
            {
                "nct_id": nct,
                "title": ident.get("briefTitle"),
                "status": status.get("overallStatus"),
                "phase": design.get("phases", []),
                "conditions": ps.get("conditionsModule", {}).get("conditions", []),
                "interventions": [i.get("name") for i in ps.get("armsInterventionsModule", {}).get("interventions", [])],
                "url": f"https://clinicaltrials.gov/study/{nct}",
            }
        )
    return {"condition": condition, "count": len(studies), "studies": studies}
