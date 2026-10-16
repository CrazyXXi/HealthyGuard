"""Pilier Équité : mêmes scénarios cliniques posés par des profils d'utilisateurs différents.

Chaque cas est jugé individuellement ici. La comparaison entre profils (écarts de taux de
réussite, test statistique) est calculée dans `healthy_guard.stats`.
"""
from healthy_guard.checks.base import make_run
from healthy_guard.models import Pillar

DATASET = "equite"
PILLAR = Pillar.EQUITY
run = make_run(DATASET, PILLAR)
