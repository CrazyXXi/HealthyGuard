"""Orchestration de l'audit : exécute les piliers, vérifie la traçabilité, décide."""
from __future__ import annotations

import time
from typing import Callable

from healthy_guard.adapters.base import ChatbotAdapter
from healthy_guard.checks import (accountability, clinical_safety, consent, equity, privacy,
                                  transparency)
from healthy_guard.datasets import load_dataset
from healthy_guard.judges.base import Judge
from healthy_guard.judges.keyword import KeywordJudge
from healthy_guard.models import AuditReport, Pillar
from healthy_guard.policy import decide_with_reasons

# Piliers qui interrogent le chatbot. Le pilier Responsabilité, lui, audite l'audit.
PILLAR_MODULES = {
    Pillar.CLINICAL_SAFETY: clinical_safety,
    Pillar.TRANSPARENCY: transparency,
    Pillar.EQUITY: equity,
    Pillar.PRIVACY: privacy,
    Pillar.CONSENT: consent,
}
ALL_PILLARS = list(PILLAR_MODULES) + [Pillar.ACCOUNTABILITY]

ProgressCallback = Callable[[Pillar, int, int], None]


def sample(cases: list[dict], limit: int | None) -> list[dict]:
    """Échantillon déterministe réparti uniformément (pas les N premiers : ils se ressemblent)."""
    if limit is None or limit >= len(cases):
        return cases
    if limit <= 0:
        return []
    step = len(cases) / limit
    return [cases[int(i * step)] for i in range(limit)]


class Auditor:
    def __init__(
        self,
        adapter: ChatbotAdapter,
        judge: Judge | None = None,
        pillars: list[Pillar] | None = None,
        repeats: int = 1,
        limit: int | None = None,
        max_major_fails: int = 3,
        progress: ProgressCallback | None = None,
        extra_cases: dict[Pillar, list[dict]] | None = None,
        extra_info: dict | None = None,
        warnings: list[str] | None = None,
        only_ids: dict[Pillar, set[str]] | None = None,
        concurrency: int = 1,
    ):
        if repeats < 1:
            raise ValueError("repeats doit être >= 1")
        self.adapter = adapter
        self.judge = judge or KeywordJudge()
        self.pillars = list(dict.fromkeys(pillars or ALL_PILLARS))
        self.repeats = repeats
        self.limit = limit
        self.max_major_fails = max_major_fails
        self.progress = progress
        self.extra_cases = extra_cases or {}
        self.extra_info = extra_info
        self.warnings = warnings or []
        self.only_ids = only_ids  # relance ciblée (voir retry.py) : exécute seulement ces cas, sans échantillonnage
        self.concurrency = concurrency

    def run(self) -> AuditReport:
        interrogated = [p for p in self.pillars if p in PILLAR_MODULES]
        datasets = [PILLAR_MODULES[p].DATASET for p in interrogated]
        metadata = accountability.build_metadata(
            self.adapter, self.judge, datasets,
            settings={
                "pillars": [p.value for p in self.pillars],
                "repeats": self.repeats,
                "limit_per_pillar": self.limit,
                "max_major_fails": self.max_major_fails,
                "concurrency": self.concurrency,
            },
        )
        if self.extra_info:
            metadata["extra_cases"] = self.extra_info  # fichier fourni par l'utilisateur : tracé
        if self.warnings:
            metadata["warnings"] = self.warnings
        started = time.perf_counter()
        report = AuditReport(metadata=metadata)

        for pillar in interrogated:
            module = PILLAR_MODULES[pillar]
            extra = self.extra_cases.get(pillar, [])  # jamais échantillonnés : l'utilisateur les veut tous
            if self.only_ids is not None:
                wanted = self.only_ids.get(pillar, set())
                cases = [c for c in load_dataset(module.DATASET) + extra if c["id"] in wanted]
            else:
                cases = sample(load_dataset(module.DATASET), self.limit) + extra
            metadata["datasets"][module.DATASET]["cases_run"] = len(cases)
            callback = (lambda done, total, p=pillar: self.progress(p, done, total)) \
                if self.progress else None
            report.results += module.run(
                self.adapter, cases=cases, judge=self.judge,
                repeats=self.repeats, progress=callback, concurrency=self.concurrency,
            )

        if Pillar.ACCOUNTABILITY in self.pillars:
            report.results += accountability.check_traceability(report, self.pillars)

        decision, reasons = decide_with_reasons(report.results, self.max_major_fails)
        report.decision = decision
        metadata["decision_reasons"] = reasons
        metadata["finished_at"] = accountability.now_iso()
        metadata["duration_s"] = round(time.perf_counter() - started, 2)
        return report
