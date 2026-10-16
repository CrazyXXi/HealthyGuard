"""Auditer VOTRE chatbot avant publication. Copiez ce fichier, gardez la ligne qui correspond à votre cas, supprimez les autres.

    python examples/02_votre_chatbot.py
"""
import healthy_guard as hg

PROMPT = open("prompt_systeme.txt", encoding="utf-8").read()          # le prompt système de votre chatbot (facultatif mais recommandé)

# --- 1. Un modèle derrière une API (OpenAI, Gemini, Mistral...) : la clé reste dans une variable d'environnement
bot = hg.openai_chatbot("gemini-3.1-flash-lite", base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                        api_key_env="GEMINI_API_KEY")

# --- 2. Un modèle LOCAL (Ollama, LM Studio...) : aucune clé nécessaire
# bot = hg.local_chatbot("llama3.1")

# --- 3. Votre propre serveur (Flask, FastAPI, Rasa...)
# bot = hg.rest_chatbot("http://localhost:5005/webhooks/rest/webhook", body_template={"sender": "{{session}}", "message": "{{message}}"},
#                       response_path="*.text")

# --- 4. Une fonction Python : elle reçoit la liste des messages et renvoie le texte de la réponse
# def mon_chatbot(messages):
#     return "Réponse de mon chatbot"
# bot = hg.function_chatbot(mon_chatbot)

release = hg.audit(
    bot,
    system_prompt=PROMPT,
    name="Mon assistant santé",
    intended_use="Informations générales de santé pour le grand public",
    audience="grand public francophone",
    limit=8,                  # essai rapide ; enlevez cette ligne pour l'audit complet (plusieurs centaines de scénarios)
    concurrency=4,            # 4 scénarios en même temps
    output_dir="reports/mon_assistant",
)

# Dans vos tests ou votre intégration continue : lève une exception si le chatbot ne peut pas être publié.
# hg.gate(bot, system_prompt=PROMPT)
