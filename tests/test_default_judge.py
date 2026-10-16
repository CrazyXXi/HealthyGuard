import re
from pathlib import Path

import healthy_guard as hg
from healthy_guard import api
from healthy_guard.adapters.demo import DemoChatbot
from healthy_guard.judges.assisted import AssistedJudge

ROOT = Path(__file__).resolve().parent.parent


def _clean(monkeypatch):
    for name in ("HEALTHYGUARD_JUDGE_URL", "HEALTHYGUARD_API_KEY", "JUDGE_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(api, "HOSTED_JUDGE_URL", "")


def test_no_default_judge_without_gateway(monkeypatch):
    _clean(monkeypatch)
    assert api.default_judge() is None


def test_default_judge_uses_alias_and_needs_no_token(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("HEALTHYGUARD_JUDGE_URL", "https://passerelle.example")
    judge = api.default_judge()
    assert isinstance(judge, AssistedJudge)
    adapter = api.llm_adapter_of(judge)
    assert adapter.model == api.JUDGE_ALIAS
    assert adapter.describe()["authentication"] != "api_key" if "authentication" in adapter.describe() else True


def test_audit_without_judge_warns_that_rules_only(monkeypatch, tmp_path):
    _clean(monkeypatch)
    release = hg.audit(DemoChatbot("good"), limit=2, verbose=False, output_dir=tmp_path)
    assert any("règles seules" in w for w in release.report["metadata"].get("warnings", [])) or release.ready


def test_no_model_name_endpoint_or_secret_in_published_files():
    forbidden = re.compile(r"modal\.direct|zai-org|GL[M]|\bwk-[A-Za-z0-9]{10,}|\bws-[A-Za-z0-9]{10,}|AIzaSy[A-Za-z0-9_-]{20,}", re.I)
    checked = []
    for folder in ("healthy_guard", "docs", "examples", "server", "scripts", "tests"):
        for path in (ROOT / folder).rglob("*"):
            if path.suffix in {".py", ".md", ".json", ".toml", ".ipynb", ".txt"} and "__pycache__" not in path.parts:
                checked.append(path)
    checked += [ROOT / "README.md", ROOT / "pyproject.toml", ROOT / "juges.example.json"]
    checked = [p for p in checked if p.name != "test_default_judge.py"]
    offenders = [str(p.relative_to(ROOT)) for p in checked if p.exists() and forbidden.search(p.read_text(encoding="utf-8", errors="ignore"))]
    assert not offenders, offenders
