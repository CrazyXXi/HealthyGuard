"""Pilier Transparence : le chatbot se présente-t-il comme une IA, reconnaît-il ses limites,
exprime-t-il son incertitude, évite-t-il les diagnostics fermes ?"""
from healthy_guard.checks.base import make_run
from healthy_guard.models import Pillar

DATASET = "transparence"
PILLAR = Pillar.TRANSPARENCY
run = make_run(DATASET, PILLAR)
