from healthy_guard.adapters.demo import DemoChatbot
from healthy_guard.auditor import Auditor
from healthy_guard.interpretation import format_interpretation
from healthy_guard.judges.keyword import KeywordJudge


def _stats(kind):
    report = Auditor(DemoChatbot(kind), KeywordJudge(), limit=6).run()
    from healthy_guard.stats import compute_stats
    return compute_stats(report)


def test_every_pillar_is_explained_in_words():
    reading = _stats("flawed")["interpretation"]
    assert {"clinical_safety", "equity", "privacy"} <= set(reading["pillars"])
    for pillar in reading["pillars"].values():
        assert pillar["question"] and pillar["details"] and pillar["level"] and pillar["reliability"] in {"faible", "moyenne", "bonne"}
    assert reading["how_to_read"]


def test_small_sample_is_never_called_solid():
    for pillar in _stats("good")["interpretation"]["pillars"].values():
        assert pillar["level"] != "satisfaisant"          # 6 cas : jamais « solide »
        assert pillar["reliability"] == "faible"


def test_critical_failure_is_worrying_and_equity_warns_low_power():
    reading = _stats("flawed")["interpretation"]
    levels = {p["level"] for p in reading["pillars"].values()}
    assert "préoccupant" in levels or "à corriger" in levels
    text = format_interpretation(reading)
    assert "Lecture des résultats" in text
    assert "ne veut PAS dire" in " ".join(reading["pillars"]["equity"]["details"])
