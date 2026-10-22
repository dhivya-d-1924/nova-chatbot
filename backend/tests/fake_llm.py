"""A small OpenAI-compatible test server (chat completions with streaming, tool calls, usage; embeddings).

Used only by the automated tests and the browser end-to-end tests so they exercise the real HTTP path through
the OpenAI SDK without a paid key. Behaviour is driven by keywords in the latest user message:
  "calc <expr>"    -> calls the calculate tool, then answers with its result
  "app <tool> {json}" -> calls any offered tool (e.g. an internal app action) with those arguments
  "email <addr>"   -> calls send_email (needs approval)
  "slow"           -> waits 2 s between stream chunks
  "fail500"        -> HTTP 500
  "failmid"        -> stream breaks after the first chunk
  otherwise        -> "Echo: <message>" (citing [1] when sources are present)

Run standalone:  python tests/fake_llm.py 8811
"""
import hashlib
import json
import re
import sys
import threading
import time

from flask import Flask, Response, jsonify, request

app = Flask(__name__)
STATE = {"calls": 0, "last_request": None}


def _last_user(msgs):
    for m in reversed(msgs):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, list):
                return " ".join(p.get("text", "") for p in c if p.get("type") == "text")
            return c or ""
    return ""


def _reply_for(body):
    msgs = body.get("messages", [])
    last = _last_user(msgs)
    system = msgs[0].get("content", "") if msgs and msgs[0].get("role") == "system" else ""
    tools = {t["function"]["name"] for t in body.get("tools") or []}
    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    if "decide whether it is about someone being absent" in system:
        body = last.split("<untrusted>")[-1].lower()
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", body)
        sender = re.search(r"sender: ([^<\n]*)<([^>]+)>", last.lower())
        if not dates or not any(w in body for w in ("leave", "sick", "vacation", "out of office")):
            return {"text": '{"absence": false}'}
        kind = "approved" if "approved" in body else ("request" if "request" in body else "announcement")
        return {"text": json.dumps({"absence": True, "person_name": (sender.group(1).strip().title() if sender else ""),
                                    "person_email": sender.group(2) if sender else "", "kind": kind,
                                    "start_date": dates[0], "end_date": dates[-1]})}
    content = last.split("<untrusted>")[-1].split("</untrusted>")[0]
    if "Classify each email" in system:       # workspace email triage
        out = []
        for i, line in re.findall(r"^\[(\d+)\] (.*)$", content, re.M):
            low = line.lower()
            cat = "urgent" if "urgent" in low else "low" if "no-reply" in low else "needs_reply" if "?" in line else "fyi"
            out.append({"i": int(i), "category": cat, "reason": f"fake {cat}"})
        return {"text": json.dumps(out)}
    if "Extract concrete action items" in system:   # action items from email/Teams
        items = []
        for i, block in re.findall(r"### SOURCE (\d+)\n(.*?)(?=### SOURCE|\Z)", content, re.S):
            for sentence in re.findall(r"(?:Can|Could) you ([^?]+)\?", block):
                due = "2026-10-23" if "friday" in sentence.lower() else ""
                owner = "Ravi" if "Ravi will own" in block else ""
                items.append({"source": int(i), "title": sentence.strip().capitalize()[:80], "owner": owner, "due": due})
        items.append({"source": 999, "title": "out of range is ignored"})
        return {"text": json.dumps(items)}
    if "professional email" in system or "short Teams update" in system:   # cross-platform drafts
        subject = (re.search(r"Subject: (.*)", content) or [None, "Teams discussion"])[1].strip()
        gist = re.sub(r"\s+", " ", content.split("\n\n", 1)[-1])[:160]
        extra = {"to": "attacker@evil.example"} if "attacker" in content else {}   # the model can't add recipients
        if "professional email" in system:
            return {"text": json.dumps({"subject": f"Re: {subject}", "body": f"Hello,\n\nSummary: {gist}\n\nThanks", **extra})}
        return {"text": json.dumps({"text": f"Update from email “{subject}”: {gist}", **extra})}
    if "study planner" in system:
        n = int((re.search(r"Days: (\d+)", last) or [0, 5])[1])
        return {"text": "Here is your plan:\n```json\n" + json.dumps({"days": [
            {"day": i + 1, "title": f"Part {i + 1}", "topics": [f"Topic {i + 1}"], "goals": ["Understand it"],
             "tasks": ["Read the notes", "Practise"], "minutes": 90} for i in range(n)]}) + "\n```"}
    if "study material writer" in system:
        return {"text": "## Overview\nPhotosynthesis turns light into energy [1].\n## Practice questions\n1. What is it? Answer: [1]"}
    if "patient tutor" in system and "Sources:" in last:
        if "unrelated" in last:
            return {"text": "I couldn't find that in your documents."}
        return {"text": "From your material: chlorophyll absorbs light [1]."}
    if "running summary" in system:
        return {"text": "SUMMARY: the user and assistant talked about earlier topics."}
    if "You grade answers" in system:
        return {"text": "4"}
    if tool_msgs:
        content = tool_msgs[-1]["content"]
        inner = re.sub(r"</?untrusted[^>]*>", "", content).strip()
        return {"text": f"Tool result: {inner[:300]}"}
    m = re.search(r"app (\S+) (\{.*\})", last)
    if m and m.group(1) in tools:
        return {"tool": (m.group(1), json.loads(m.group(2)))}
    m = re.search(r"calc (.+)", last)
    if m and "calculate" in tools:
        return {"tool": ("calculate", {"expression": m.group(1).strip()})}
    m = re.search(r"email (\S+@\S+)", last)
    if m and "send_email" in tools:
        return {"tool": ("send_email", {"to": m.group(1), "subject": "Hello", "body": "Hi from the test."})}
    if "what time" in last.lower() and "current_time" in tools:
        return {"tool": ("current_time", {"timezone": "Asia/Kolkata"})}
    if "Sources:" in last:
        return {"text": "According to your document, the answer is in the text [1]."}
    if "capital of France" in last:
        return {"text": "The capital of France is Paris."}
    if "48213" in last:
        return {"text": "48213 multiplied by 9127 is 440,040,051."}
    return {"text": f"Echo: {last[:500]}"}


def _usage(body, text):
    prompt = sum(len(json.dumps(m.get("content", ""))) for m in body.get("messages", [])) // 4
    return {"prompt_tokens": prompt, "completion_tokens": max(1, len(text) // 4), "total_tokens": prompt + len(text) // 4}


@app.post("/v1/chat/completions")
def completions():
    body = request.get_json()
    STATE["calls"] += 1
    STATE["last_request"] = body
    last = _last_user(body.get("messages", []))
    if "fail500" in last:
        return jsonify(error={"message": "internal failure"}), 500
    r = _reply_for(body)
    created = int(time.time())
    if "slow" in last and not body.get("stream"):
        time.sleep(4)
    if r.get("tool"):
        name, args = r["tool"]
        call = {"id": f"call_{STATE['calls']}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        if body.get("stream"):
            def gen_tool():
                chunk = {"id": "c", "object": "chat.completion.chunk", "created": created, "model": body["model"],
                         "choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [{"index": 0, **call}]},
                                      "finish_reason": "tool_calls"}]}
                yield f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n"
            return Response(gen_tool(), mimetype="text/event-stream")
        return jsonify({"id": "c", "object": "chat.completion", "created": created, "model": body["model"],
                        "choices": [{"index": 0, "finish_reason": "tool_calls",
                                     "message": {"role": "assistant", "content": None, "tool_calls": [call]}}],
                        "usage": _usage(body, "")})
    text = r["text"]
    if not body.get("stream"):
        return jsonify({"id": "c", "object": "chat.completion", "created": created, "model": body["model"],
                        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
                        "usage": _usage(body, text)})
    include_usage = (body.get("stream_options") or {}).get("include_usage")
    pieces = re.findall(r"\S+\s*", text) or [text]

    def gen():
        for i, p in enumerate(pieces):
            chunk = {"id": "c", "object": "chat.completion.chunk", "created": created, "model": body["model"],
                     "choices": [{"index": 0, "delta": {"content": p}, "finish_reason": None}]}
            yield f"data: {json.dumps(chunk)}\n\n"
            if "failmid" in last and i == 0:
                raise RuntimeError("simulated provider disconnect")
            if "slow" in last:
                time.sleep(2)
        head = {"id": "c", "object": "chat.completion.chunk", "created": created, "model": body["model"]}
        yield f"data: {json.dumps({**head, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
        if include_usage:
            yield f"data: {json.dumps({**head, 'choices': [], 'usage': _usage(body, text)})}\n\n"
        yield "data: [DONE]\n\n"
    return Response(gen(), mimetype="text/event-stream")


def _vec(text, dims=64):
    v = [0.0] * dims
    for w in re.findall(r"\w+", text.lower()):
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % dims] += 1.0
    return v


@app.post("/v1/embeddings")
def embeddings():
    body = request.get_json()
    inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
    return jsonify({"object": "list", "model": body["model"],
                    "data": [{"object": "embedding", "index": i, "embedding": _vec(t)} for i, t in enumerate(inputs)],
                    "usage": {"prompt_tokens": sum(len(t) // 4 for t in inputs), "total_tokens": sum(len(t) // 4 for t in inputs)}})


def serve_in_thread(port=0):
    """Start the server on a free port; returns (base_url, server)."""
    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_port}/v1", srv


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8811
    app.run(host="127.0.0.1", port=port, threaded=True)
