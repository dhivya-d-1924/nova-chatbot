"""Study mode: subjects, material, N-day plan, notes, restricted chat and the open doubts index."""
import io

from tests.conftest import Api
from tests.fake_llm import STATE

SYLLABUS = (b"Biology syllabus. Unit 1: Photosynthesis and chlorophyll. Unit 2: Respiration in cells. "
            b"Unit 3: Genetics and DNA. Unit 4: Ecology and food chains.")
MATERIAL = (b"Photosynthesis happens in chloroplasts. Chlorophyll absorbs light energy and plants make glucose "
            b"from carbon dioxide and water. Respiration releases energy from glucose in mitochondria.")


def upload(api, data, name):
    r = api.post("/api/documents", data={"file": (io.BytesIO(data), name)}, content_type="multipart/form-data")
    assert r.status_code in (200, 201), r.json
    return r.json["id"]


def subject_with_material(api, name="Biology"):
    s = api.post("/api/subjects", {"name": name, "description": "Class 10"}).json
    api.post(f"/api/subjects/{s['id']}/documents", {"document_id": upload(api, SYLLABUS, "syllabus.txt"), "role": "syllabus"})
    r = api.post(f"/api/subjects/{s['id']}/documents", {"document_id": upload(api, MATERIAL, "chapter1.txt")})
    assert r.status_code == 200 and r.json["materials"] == 2 and r.json["has_syllabus"]
    return s["id"]


def test_subject_crud_and_validation(api):
    api.register("student@school.test", "Student")
    assert api.post("/api/subjects", {"name": ""}).status_code == 400
    sid = api.post("/api/subjects", {"name": "Maths"}).json["id"]
    assert api.patch(f"/api/subjects/{sid}", {"name": "Mathematics"}).json["name"] == "Mathematics"
    assert [s["name"] for s in api.get("/api/subjects").json] == ["Mathematics"]
    assert api.delete(f"/api/subjects/{sid}").status_code == 200
    assert api.get(f"/api/subjects/{sid}").status_code == 404


def test_five_day_plan_from_syllabus(api):
    api.register("student@school.test", "Student")
    sid = subject_with_material(api)
    r = api.post(f"/api/subjects/{sid}/plan", {"days": 5, "minutes_per_day": 90})
    assert r.status_code == 200, r.json
    plan = r.json
    assert plan["day_count"] == 5 and [d["day"] for d in plan["days"]] == [1, 2, 3, 4, 5]
    assert all(d["topics"] and d["tasks"] and d["done"] is False for d in plan["days"])
    sent = STATE["last_request"]["messages"][1]["content"]
    assert "Days: 5" in sent and "Photosynthesis and chlorophyll" in sent and "<untrusted>" in sent
    done = api.patch(f"/api/subjects/{sid}/plan/days/2", {"done": True}).json
    assert done["done"] == 1 and done["days"][1]["done"] is True
    assert api.get("/api/subjects").json[0]["plan_progress"] == {"done": 1, "days": 5}
    assert api.post(f"/api/subjects/{sid}/plan", {"days": 30}).status_code == 400


def test_plan_needs_material(api):
    api.register("student@school.test", "Student")
    sid = api.post("/api/subjects", {"name": "Empty"}).json["id"]
    r = api.post(f"/api/subjects/{sid}/plan", {"days": 5})
    assert r.status_code == 400 and r.json["code"] == "no_material"


def test_study_notes_from_material_and_missing_topic_becomes_doubt(api):
    api.register("student@school.test", "Student")
    sid = subject_with_material(api)
    r = api.post(f"/api/subjects/{sid}/notes", {"topic": "photosynthesis chlorophyll"})
    assert r.status_code == 201 and "## Overview" in r.json["content"] and r.json["sources"]
    assert api.get(f"/api/subjects/{sid}/notes").json[0]["title"] == "photosynthesis chlorophyll"
    missing = api.post(f"/api/subjects/{sid}/notes", {"topic": "quantum chromodynamics"})
    assert missing.status_code == 422
    doubts = api.get("/api/doubts").json
    assert doubts["counts"]["open"] == 1 and "quantum chromodynamics" in doubts["doubts"][0]["question"]


def test_notes_for_a_plan_day(api):
    api.register("student@school.test", "Student")
    sid = subject_with_material(api)
    api.post(f"/api/subjects/{sid}/plan", {"days": 3})
    r = api.post(f"/api/subjects/{sid}/notes", {"day": 1})
    # day 1 of the fake plan is "Part 1: Topic 1", which isn't in the material, so it is refused and noted
    assert r.status_code == 422 and api.get("/api/doubts").json["counts"]["open"] == 1


def test_study_chat_answers_only_from_material(api):
    api.register("student@school.test", "Student")
    sid = subject_with_material(api)
    conv = api.post(f"/api/subjects/{sid}/chat").json
    assert conv["subject_id"] == sid and conv["subject"]["name"] == "Biology"
    # covered by the material: answered with a citation, tools never offered, other documents never searched
    other = upload(api, b"Secret company salary list for 2026.", "salaries.txt")
    r, ev = api.stream({"conversation_id": conv["id"], "message": "How does chlorophyll absorb light?", "tools": True,
                        "doc_ids": [other]})
    done = next(e for e in ev if e["type"] == "done")["message"]
    assert "chlorophyll absorbs light [1]" in done["content"] and done["sources"]
    req = STATE["last_request"]
    assert not req.get("tools") and "patient tutor" in req["messages"][0]["content"]
    assert "salary" not in str(req["messages"]).lower()
    assert all(s["filename"] != "salaries.txt" for s in done["sources"])

    # not in the material at all: the AI is not called and the question goes to open doubts
    calls = STATE["calls"]
    r, ev = api.stream({"conversation_id": conv["id"], "message": "Who won the football world cup final in the year 2022 in Qatar?"})
    reply = next(e for e in ev if e["type"] == "done")["message"]["content"]
    assert "couldn't find this in your Biology material" in reply and STATE["calls"] == calls
    doubts = api.get(f"/api/doubts?subject_id={sid}").json["doubts"]
    assert doubts[0]["question"] == "Who won the football world cup final in the year 2022 in Qatar?" and doubts[0]["origin"] == "chat"

    # a short follow-up is checked together with the previous question, so it still reaches the tutor
    r, ev = api.stream({"conversation_id": conv["id"], "message": "explain chlorophyll simply"})
    assert "chlorophyll absorbs light" in next(e for e in ev if e["type"] == "done")["message"]["content"]

    # matched words but the AI says the material doesn't answer: also recorded
    api.stream({"conversation_id": conv["id"], "message": "unrelated question about glucose energy"})
    assert api.get("/api/doubts").json["counts"]["open"] == 2
    # the same open question isn't added twice
    api.stream({"conversation_id": conv["id"], "message": "Who won the football world cup final in the year 2022 in Qatar?"})
    assert api.get("/api/doubts").json["counts"]["open"] == 2
    detail = api.get(f"/api/conversations/{conv['id']}").json
    assert detail["subject"] == {"id": sid, "name": "Biology"}


def test_non_streaming_study_chat_is_restricted_too(api):
    api.register("student@school.test", "Student")
    sid = subject_with_material(api)
    cid = api.post(f"/api/subjects/{sid}/chat").json["id"]
    r = api.post("/api/chat", {"conversation_id": cid, "message": "Tell me a joke about cats please"})
    assert r.status_code == 200 and "won't guess" in r.json["message"]["content"]


def test_doubts_index_resolve_reopen_delete(api):
    api.register("student@school.test", "Student")
    sid = subject_with_material(api)
    d = api.post(f"/api/subjects/{sid}/doubts", {"question": "Why are leaves green?"}).json
    r = api.patch(f"/api/doubts/{d['id']}", {"status": "resolved", "note": "Chlorophyll reflects green light."})
    assert r.json["status"] == "resolved" and r.json["resolved_at"]
    idx = api.get("/api/doubts?status=resolved").json
    assert idx["counts"] == {"open": 0, "resolved": 1} and idx["doubts"][0]["subject_name"] == "Biology"
    assert api.patch(f"/api/doubts/{d['id']}", {"status": "open"}).json["resolved_at"] is None
    assert api.delete(f"/api/doubts/{d['id']}").status_code == 200
    assert api.get("/api/doubts?status=all").json["doubts"] == []


def test_study_data_is_private(api, new_api):
    api.register("alice@school.test", "Alice")
    sid = subject_with_material(api)
    d = api.post(f"/api/subjects/{sid}/doubts", {"question": "Alice's doubt"}).json
    bob = new_api()
    bob.register("bob@school.test", "Bob")
    assert bob.get(f"/api/subjects/{sid}").status_code == 404
    assert bob.post(f"/api/subjects/{sid}/plan", {"days": 5}).status_code == 404
    assert bob.post(f"/api/subjects/{sid}/chat").status_code == 404
    assert bob.patch(f"/api/doubts/{d['id']}", {"status": "resolved"}).status_code == 404
    assert bob.get("/api/doubts?status=all").json["doubts"] == []
    bob_sid = bob.post("/api/subjects", {"name": "Bob's"}).json["id"]
    alice_doc = api.get("/api/documents").json[0]["id"]
    assert bob.post(f"/api/subjects/{bob_sid}/documents", {"document_id": alice_doc}).status_code == 404


def test_deleting_subject_or_document_cleans_up(api):
    api.register("student@school.test", "Student")
    sid = subject_with_material(api)
    cid = api.post(f"/api/subjects/{sid}/chat").json["id"]
    doc = api.get(f"/api/subjects/{sid}").json["documents"][1]["id"]
    assert api.delete(f"/api/documents/{doc}").status_code in (200, 204)
    assert api.get(f"/api/subjects/{sid}").json["materials"] == 1
    api.delete(f"/api/subjects/{sid}")
    conv = api.get(f"/api/conversations/{cid}").json
    assert conv["subject_id"] is None  # becomes a normal chat; documents stay in "Your documents"
    assert len(api.get("/api/documents").json) == 1


def test_account_export_and_delete_include_study(make_app):
    app = make_app()
    admin = Api(app)
    admin.register("admin@school.test", "Admin")
    api = Api(app)
    api.register("student@school.test", "Student")
    sid = subject_with_material(api)
    api.post(f"/api/subjects/{sid}/doubts", {"question": "A doubt"})
    exported = api.get("/api/account/export").get_json(force=True)
    assert exported["study"][0]["subject"] == "Biology" and exported["study"][0]["doubts"][0]["question"] == "A doubt"
    assert api.delete("/api/account", {"password": "Correct-Horse-9", "confirm": "DELETE"}).status_code == 204
    from nova import db
    with db.engine.connect() as c:
        assert c.execute(db.subjects.select()).all() == [] and c.execute(db.doubts.select()).all() == []
