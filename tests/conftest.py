"""Aucun test ne doit appeler le réseau : le juge intégré (service hébergé) est désactivé par défaut pour toute la suite."""
import pytest


@pytest.fixture(autouse=True)
def no_hosted_judge(monkeypatch):
    monkeypatch.setattr("healthy_guard.api.HOSTED_JUDGE_URL", "")
    for name in ("HEALTHYGUARD_JUDGE_URL", "HEALTHYGUARD_API_KEY"):
        monkeypatch.delenv(name, raising=False)
