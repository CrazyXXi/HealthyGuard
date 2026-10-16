import importlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

SERVER = Path(__file__).resolve().parent.parent / "server"
TOKEN = "cle-secrete.valeur-secrete"


class FakeUpstream(BaseHTTPRequestHandler):
    seen: list = []
    status = 200

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeUpstream.seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        payload = json.dumps({"id": "x", "model": "vrai-modele-interne", "system_fingerprint": "fp", "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "OK"}}]}).encode()
        self.send_response(FakeUpstream.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture()
def gateway(monkeypatch):
    FakeUpstream.seen, FakeUpstream.status = [], 200
    server = HTTPServer(("127.0.0.1", 0), FakeUpstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("UPSTREAM_URL", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setenv("UPSTREAM_TOKEN", TOKEN)
    monkeypatch.setenv("UPSTREAM_MODEL", "vrai-modele-interne")
    for name in ("ALLOWED_KEYS", "RATE_PER_MINUTE", "REASONING_EFFORT", "MAX_TOKENS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.syspath_prepend(str(SERVER))
    sys.modules.pop("app", None)
    module = importlib.import_module("app")
    module._hits.clear()
    yield TestClient(module.app), module
    server.shutdown()
    sys.modules.pop("app", None)


MESSAGES = {"model": "healthyguard-judge", "messages": [{"role": "user", "content": "Bonjour"}], "max_tokens": 5000}


def test_relays_with_server_side_credentials_and_hides_the_real_model(gateway):
    client, _ = gateway
    reply = client.post("/v1/chat/completions", json=MESSAGES)
    assert reply.status_code == 200
    data = reply.json()
    assert data["model"] == "healthyguard-judge" and "system_fingerprint" not in data
    assert data["choices"][0]["message"]["content"] == "OK"
    sent = FakeUpstream.seen[0]
    assert sent["path"] == "/v1/chat/completions" and sent["auth"] == "Bearer " + TOKEN
    assert sent["body"]["model"] == "vrai-modele-interne" and sent["body"]["max_tokens"] == 2048
    assert sent["body"]["reasoning_effort"] == "low"
    assert TOKEN not in reply.text and "vrai-modele-interne" not in reply.text


def test_access_keys_when_configured(gateway, monkeypatch):
    client, _ = gateway
    monkeypatch.setenv("ALLOWED_KEYS", "cle-a, cle-b")
    assert client.post("/v1/chat/completions", json=MESSAGES).status_code == 401
    assert client.post("/v1/chat/completions", json=MESSAGES, headers={"Authorization": "Bearer mauvaise"}).status_code == 401
    assert client.post("/v1/chat/completions", json=MESSAGES, headers={"Authorization": "Bearer cle-b"}).status_code == 200


def test_rate_limit_and_invalid_requests(gateway, monkeypatch):
    client, _ = gateway
    assert client.post("/v1/chat/completions", json={"messages": []}).status_code == 400
    assert client.post("/v1/chat/completions", content=b"pas du json").status_code == 400
    monkeypatch.setenv("RATE_PER_MINUTE", "2")
    codes = [client.post("/v1/chat/completions", json=MESSAGES).status_code for _ in range(4)]
    assert 429 in codes


def test_upstream_failure_is_reported_without_leaking_details(gateway):
    client, _ = gateway
    FakeUpstream.status = 500
    reply = client.post("/v1/chat/completions", json=MESSAGES)
    assert reply.status_code == 503 and TOKEN not in reply.text and "127.0.0.1" not in reply.text


def test_missing_configuration_is_a_503_not_a_leak(gateway, monkeypatch):
    client, _ = gateway
    monkeypatch.delenv("UPSTREAM_TOKEN")
    reply = client.post("/v1/chat/completions", json=MESSAGES)
    assert reply.status_code == 503 and "UPSTREAM" not in reply.text


def test_health_and_vercel_paths(gateway):
    client, _ = gateway
    assert client.get("/health").json() == {"status": "ok"}
    assert client.post("/api/v1/chat/completions", json=MESSAGES).status_code == 200


def test_the_package_judge_works_through_the_gateway(gateway, monkeypatch):
    uvicorn = pytest.importorskip("uvicorn")
    from healthy_guard import api
    client, module = gateway
    server = uvicorn.Server(uvicorn.Config(module.app, host="127.0.0.1", port=0, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    import time
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    monkeypatch.setenv("HEALTHYGUARD_JUDGE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.delenv("HEALTHYGUARD_API_KEY", raising=False)
    judge = api.default_judge()
    answer = api.llm_adapter_of(judge).send([{"role": "user", "content": "test"}])
    server.should_exit = True
    assert answer.text == "OK"
    assert FakeUpstream.seen and FakeUpstream.seen[-1]["auth"] == "Bearer " + TOKEN
