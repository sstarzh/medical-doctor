import httpx2 as httpx
import pytest

from medical_doctor import sources

LABELS = {
    "warfarin": {
        "openfda": {"generic_name": ["WARFARIN SODIUM"], "brand_name": ["COUMADIN"]},
        "drug_interactions": ["Drugs that increase bleeding risk include NSAIDs. Fluconazole increases INR. Consult labeling."],
        "boxed_warning": ["WARNING: BLEEDING RISK"],
        "set_id": "abc",
    },
    "fluconazole": {
        "openfda": {"generic_name": ["FLUCONAZOLE"], "pharm_class_epc": ["Azole Antifungal [EPC]"]},
        "drug_interactions": ["Coumarin-type anticoagulants: prothrombin time increased with warfarin."],
        "set_id": "def",
    },
}


def handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "api.fda.gov" in url:
        q = request.url.params["search"].lower()
        for name, label in LABELS.items():
            if name in q:
                return httpx.Response(200, json={"results": [label]})
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
    if "esearch" in url:
        return httpx.Response(200, json={"esearchresult": {"idlist": ["123"]}})
    if "esummary" in url:
        return httpx.Response(200, json={"result": {"123": {"title": "A trial", "source": "NEJM", "pubdate": "2024", "pubtype": ["Randomized Controlled Trial"]}}})
    if "clinicaltrials.gov" in url:
        return httpx.Response(200, json={"studies": [{"protocolSection": {"identificationModule": {"nctId": "NCT1", "briefTitle": "T"}, "statusModule": {"overallStatus": "RECRUITING"}}}]})
    return httpx.Response(500)


@pytest.fixture(autouse=True)
def mock_http(monkeypatch):
    monkeypatch.setattr(sources, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_drug_label():
    r = await sources.drug_label("warfarin", ["boxed_warning"])
    assert r["found"] and "BLEEDING" in r["sections"]["boxed_warning"]
    assert (await sources.drug_label("unobtainium"))["found"] is False


async def test_interactions_bidirectional():
    r = await sources.drug_interactions(["warfarin", "fluconazole"])
    pairs = {(f["label_of"], f["mentions"]) for f in r["interactions"]}
    assert ("warfarin", "fluconazole") in pairs and ("fluconazole", "warfarin") in pairs


async def test_pubmed_and_trials():
    p = await sources.pubmed_search("af anticoagulation", reviews_and_trials_only=True)
    assert p["articles"][0]["pmid"] == "123" and "systematic review" in p["query"]
    t = await sources.clinical_trials("sepsis")
    assert t["studies"][0]["nct_id"] == "NCT1"
