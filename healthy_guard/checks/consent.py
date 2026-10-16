"""Pilier Information & Consentement : le chatbot présente-t-il correctement ses limites, l'usage
de l'IA et les droits de l'utilisateur, sans promesse invérifiable ni action prétendue ?"""
from healthy_guard.checks.base import make_run
from healthy_guard.models import Pillar

DATASET = "consentement"
PILLAR = Pillar.CONSENT
run = make_run(DATASET, PILLAR)
