"""Premier essai : aucun chatbot à brancher, aucune clé. Un faux chatbot « avec défauts » est audité pour montrer ce que produit HealthyGuard.

    python examples/01_essai_sans_cle.py
"""
import healthy_guard as hg

release = hg.audit(hg.demo_chatbot("flawed"), name="Chatbot de démonstration", limit=8, output_dir="reports/demo")

print()
print("Décision :", release.decision)          # pass / review_required / blocked
print("Rapport HTML :", release.paths.get("html"))
