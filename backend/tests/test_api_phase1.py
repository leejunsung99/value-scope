from app import api as api_module
from app.models import PeerVersionMember
from app.services.ai import AIResult
from tests.conftest import FakeDart

TARGET = "00000001"


def _create(client, corp_code=TARGET):
    return client.post("/api/projects", json={"corp_code": corp_code})


def _search(client, pid):
    r = client.post(f"/api/projects/{pid}/peer-search")
    assert r.status_code == 202
    sessions = client.get(f"/api/projects/{pid}/peer-search/sessions").json()
    return client.get(f"/api/projects/{pid}/peer-search/sessions/{sessions[0]['id']}").json()


def test_register_and_duplicate(client):
    r = _create(client)
    assert r.status_code == 201
    p = r.json()
    assert p["company"]["name"] == "선익시스템" and p["company"]["is_verified"]
    dup = _create(client)
    assert dup.status_code == 409
    assert dup.json()["detail"]["existing_project_id"] == p["id"]
    # reopen / list
    assert client.get(f"/api/projects/{p['id']}").json()["id"] == p["id"]
    assert [x["id"] for x in client.get("/api/projects", params={"q": "선익"}).json()] == [p["id"]]
    # search marks the existing project
    hits = client.get("/api/companies/search", params={"q": "선익"}).json()
    assert hits[0]["existing_project_id"] == p["id"]


def test_search_by_stock_code(client):
    hits = client.get("/api/companies/search", params={"q": "171090"}).json()
    assert hits[0]["corp_code"] == TARGET


def test_peer_search_without_ai(client):
    pid = _create(client).json()["id"]
    s = _search(client, pid)
    assert s["status"] == "succeeded"
    by_name = {c["company"]["name"]: c for c in s["candidates"]}
    # seeds verified against registry (AP시스템 -> DART 등록명 에이피시스템)
    assert by_name["야스"]["verification_status"] == "verified"
    assert by_name["에이피시스템"]["origin"] == "seed"
    # foreign unlisted seed stays unverified & not for valuation
    canon = by_name["캐논토키"]
    assert canon["verification_status"] == "unverified" and canon["use_type"] == "tech_competition"
    # newly discovered via business report mention, with verbatim evidence
    new = by_name["테스트전자"]
    assert new["origin"] == "report_mention"
    assert any("테스트전자" in (src.get("quote") or "") for src in new["sources"])
    # 'LG' must not be detected inside 'LG디스플레이'
    assert "LG" not in by_name
    # no AI -> qualitative criteria unassessed -> no score (not zero)
    assert by_name["야스"]["score"] is None
    assert any("AI 분류·평가 생략" in m["message"] for m in s["messages"])

    peers = client.get(f"/api/projects/{pid}/peers").json()
    assert all(m["status"] == "proposed" for m in peers["members"])


class FakeAI:
    name = "fake"
    model = "fake-model"

    def complete_json(self, system, prompt, schema):
        return AIResult(provider="fake", model="fake-model", data={
            "target_summary": {"main_business": "OLED 증착장비", "main_products": "증착기", "customers": "패널사",
                               "revenue_structure": "수주"},
            "candidates": [
                {"name": "야스", "stock_code": "255440", "category": "A", "main_business": "증착장비",
                 "main_products": "증착기", "similarity": "동일 제품", "selection_reason": "경쟁사로 언급",
                 "caution": "고객 편중", "is_new_suggestion": False,
                 "evidence_quotes": ["경쟁사로는 캐논토키와 야스가 있으며", "이 문장은 원문에 없는 가짜 인용입니다"],
                 "criteria": [{"key": "product", "level": "1", "rationale": "동일 증착장비"},
                              {"key": "process", "level": "1", "rationale": "동일 공정"},
                              {"key": "customer", "level": "0.5", "rationale": "일부 중복"},
                              {"key": "revenue", "level": "unknown", "rationale": "근거 없음"}]},
            ],
        })


def test_peer_search_with_ai_validates_quotes(client, monkeypatch):
    monkeypatch.setattr(api_module, "make_ai", lambda: FakeAI())
    pid = _create(client).json()["id"]
    s = _search(client, pid)
    yas = next(c for c in s["candidates"] if c["company"]["name"] == "야스")
    quotes = [src.get("quote") for src in yas["sources"] if src.get("quote")]
    assert "경쟁사로는 캐논토키와 야스가 있으며" in quotes
    assert not any("가짜 인용" in q for q in quotes)
    assert any("확인되지 않아" in m["message"] for m in s["messages"])
    # assessed: product30, process20, customer15(0.5), data10 -> (30+20+7.5+10)/75
    assert yas["score"] == round(67.5 / 75 * 100, 1)
    revenue = next(b for b in yas["score_breakdown"] if b["key"] == "revenue")
    assert revenue["level"] is None and revenue["level_label"] == "미평가"


def test_edit_confirm_versions(client, db):
    pid = _create(client).json()["id"]
    _search(client, pid)
    peers = client.get(f"/api/projects/{pid}/peers").json()
    ids = {m["company"]["name"]: m["id"] for m in peers["members"]}

    # confirming with nothing included is rejected
    assert client.post(f"/api/projects/{pid}/peers/confirm", json={}).status_code == 422

    r = client.post(f"/api/projects/{pid}/peers/bulk-status",
                    json={"membership_ids": [ids["야스"], ids["힘스"], ids["캐논토키"]], "status": "included"})
    assert r.status_code == 200
    # unlisted company cannot be used for valuation
    r = client.patch(f"/api/projects/{pid}/peers/{ids['캐논토키']}", json={"use_type": "valuation"})
    assert r.status_code == 422
    # category change
    r = client.patch(f"/api/projects/{pid}/peers/{ids['힘스']}", json={"category": "D"})
    assert next(m for m in r.json()["members"] if m["id"] == ids["힘스"])["category"] == "D"

    v1 = client.post(f"/api/projects/{pid}/peers/confirm", json={"note": "first"}).json()
    assert v1["version_no"] == 1 and v1["member_count"] == 3
    assert client.get(f"/api/projects/{pid}").json()["current_step"] == 2
    # no change -> no new version
    assert client.post(f"/api/projects/{pid}/peers/confirm", json={}).status_code == 409

    # exclude, restore, user-add -> v2
    client.patch(f"/api/projects/{pid}/peers/{ids['힘스']}", json={"status": "excluded"})
    client.patch(f"/api/projects/{pid}/peers/{ids['힘스']}", json={"status": "included"})
    client.patch(f"/api/projects/{pid}/peers/{ids['캐논토키']}", json={"status": "excluded"})
    r = client.post(f"/api/projects/{pid}/peers", json={"corp_code": "00000005", "category": "B"})
    assert r.status_code == 409  # 비아트론 already proposed by seed search
    r = client.post(f"/api/projects/{pid}/peers", json={"manual_name": "해외테스트社", "manual_country": "US",
                                                         "category": "E"})
    assert r.status_code == 201
    peers = r.json()
    assert peers["draft_dirty"]
    # adding the client itself is rejected
    assert client.post(f"/api/projects/{pid}/peers", json={"corp_code": TARGET, "category": "A"}).status_code == 422

    v2 = client.post(f"/api/projects/{pid}/peers/confirm", json={}).json()
    assert v2["version_no"] == 2
    names_v2 = {m["snapshot"]["company"]["name"] for m in v2["members"]}
    assert names_v2 == {"야스", "힘스", "해외테스트社"}

    # v1 untouched
    v1_again = client.get(f"/api/projects/{pid}/peers/versions/1").json()
    assert {m["snapshot"]["company"]["name"] for m in v1_again["members"]} == {"야스", "힘스", "캐논토키"}
    hims_v1 = next(m for m in v1_again["members"] if m["snapshot"]["company"]["name"] == "힘스")
    assert hims_v1["category"] == "D"
    assert db.query(PeerVersionMember).count() == 6


def test_research_preserves_user_edits(client):
    pid = _create(client).json()["id"]
    _search(client, pid)
    peers = client.get(f"/api/projects/{pid}/peers").json()
    yas = next(m for m in peers["members"] if m["company"]["name"] == "야스")
    client.patch(f"/api/projects/{pid}/peers/{yas['id']}", json={"status": "excluded", "category": "E"})
    _search(client, pid)
    peers = client.get(f"/api/projects/{pid}/peers").json()
    yas2 = next(m for m in peers["members"] if m["company"]["name"] == "야스")
    assert (yas2["status"], yas2["category"]) == ("excluded", "E")
    assert len(client.get(f"/api/projects/{pid}/peer-search/sessions").json()) == 2


def test_incremental_document_reuse(client, fake_dart):
    pid = _create(client).json()["id"]
    _search(client, pid)
    docs_first = [c for c in fake_dart.calls if c.startswith("doc:")]
    _search(client, pid)
    docs_second = [c for c in fake_dart.calls if c.startswith("doc:")][len(docs_first):]
    assert docs_first and docs_second == []  # same rcept_no -> no re-download


def test_without_dart_key(engine, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services.ai import NoAIProvider

    monkeypatch.setattr(api_module, "make_dart", lambda: FakeDart(configured=False))
    monkeypatch.setattr(api_module, "make_ai", lambda: NoAIProvider())
    with TestClient(app) as c:
        r = c.get("/api/companies/search", params={"q": "선익"})
        assert r.status_code == 503 and "DART_API_KEY" in r.json()["detail"]
        r = c.post("/api/projects", json={"manual": {"name": "선익시스템", "stock_code": "171090", "market": "KOSDAQ"}})
        assert r.status_code == 201
        pid = r.json()["id"]
        assert r.json()["company"]["is_verified"] is False
        s = _search(c, pid)
        assert s["status"] == "partial"
        assert any(m["level"] == "error" and "DART_API_KEY" in m["message"] for m in s["messages"])
        assert all(x["verification_status"] == "unverified" for x in s["candidates"])
        # duplicate manual registration by stock code
        r = c.post("/api/projects", json={"manual": {"name": "선익", "stock_code": "171090"}})
        assert r.status_code == 409


def test_system_status_hides_secrets(client):
    s = client.get("/api/system/status").json()
    assert set(s["integrations"]) >= {"dart", "krx", "ai_provider"}
    assert all(isinstance(v, (bool, str)) for v in s["integrations"].values())
