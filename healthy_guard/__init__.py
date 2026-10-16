"""HealthyGuard : audit de gouvernance et d'éthique d'un chatbot de santé, AVANT sa mise en ligne.

    import healthy_guard as hg
    release = hg.audit(mon_chatbot, system_prompt=mon_prompt)      # connexion, prompt, comportement, recommandations
    hg.gate(mon_chatbot, system_prompt=mon_prompt)                 # lève AuditFailed si le chatbot ne peut pas être publié

Rien ne s'exécute à l'import (un audit appelle le réseau et coûte du temps) ; les fonctions sont chargées à la demande.
"""
__version__ = "0.1.0"

_PUBLIC = {
    "demo_chatbot", "audit", "gate", "check", "AuditFailed", "Chatbot", "Release", "openai_chatbot", "anthropic_chatbot", "rest_chatbot", "http_chatbot",
    "function_chatbot", "command_chatbot", "local_chatbot", "assisted_judge", "review_prompt", "review_system_prompt_text", "build_recommendations",
}


def __getattr__(name: str):
    if name in _PUBLIC:
        from healthy_guard import api
        return getattr(api, name)
    raise AttributeError(f"module 'healthy_guard' has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | _PUBLIC)
