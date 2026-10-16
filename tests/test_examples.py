import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_quickstart_example_runs_without_key_or_network(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for name in ("HEALTHYGUARD_JUDGE_URL", "HEALTHYGUARD_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    runpy.run_path(str(ROOT / "examples" / "01_essai_sans_cle.py"), run_name="__main__")
    out = capsys.readouterr().out
    assert "Décision : blocked" in out and (tmp_path / "reports" / "demo").exists()


def test_other_examples_are_valid_python():
    for path in (ROOT / "examples").glob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
