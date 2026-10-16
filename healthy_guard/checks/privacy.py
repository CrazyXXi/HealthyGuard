"""Pilier Confidentialité : le chatbot refuse-t-il de divulguer des données sensibles, résiste-t-il
aux manipulations, et évite-t-il de recopier les identifiants que l'utilisateur lui donne ?"""
from healthy_guard.checks.base import make_run
from healthy_guard.models import Pillar

DATASET = "confidentialite"
PILLAR = Pillar.PRIVACY
run = make_run(DATASET, PILLAR)
