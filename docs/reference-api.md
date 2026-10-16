# Référence Python

```python
import healthy_guard as hg
```

Rien ne s'exécute à l'import : un audit appelle le réseau et prend du temps, donc les fonctions sont chargées à la demande.

## Auditer

| Fonction | Rôle |
|---|---|
| `hg.audit(chatbot, *, system_prompt=None, system_prompt_file=None, judge=None, pillars=None, limit=None, repeats=1, concurrency=1, max_major_fails=3, output_dir=None, name="audit", verbose=True, check_connection=True, review_system_prompt=True, review_prompt_with_judge=False, extra_cases=None, extra_info=None, **fiche)` | Audit complet : connexion, revue du prompt, scénarios, recommandations, rapports. Retourne un `Release`. `**fiche` : `name`, `description`, `intended_use`, `audience`, `languages`, `tools`. |
| `hg.gate(chatbot, *, allow_review=False, **options)` | Comme `audit`, mais **lève `AuditFailed`** si le chatbot ne peut pas être publié. Pour les tests et l'intégration continue. |
| `hg.check(chatbot)` | Diagnostic du branchement, sans audit. Retourne une liste de constats. |
| `hg.review_prompt(texte)` | Revue statique d'un prompt système. |
| `hg.build_recommendations(...)` | Recommandations à partir de résultats. |

`chatbot` peut être : un `Chatbot`, un adaptateur (ci-dessous), une fonction `fn(messages) -> texte`, un profil (dictionnaire) ou `"fichier.py:fonction"`.

## Construire un chatbot

| Fonction | Cas |
|---|---|
| `hg.openai_chatbot(model, *, base_url=None, api_key_env=…, system=None, temperature=0.0, max_tokens=None, extra_body=None, **réseau)` | Modèle derrière une API de type OpenAI (OpenAI, Gemini, Mistral, vLLM…). Clé lue dans `api_key_env`. |
| `hg.anthropic_chatbot(model, *, api_key_env="ANTHROPIC_API_KEY", …)` | Anthropic. |
| `hg.local_chatbot(model, *, base_url="http://localhost:11434/v1", **options)` | Modèle local sans clé (Ollama, LM Studio, llama.cpp). |
| `hg.http_chatbot(url, **options)` | Serveur qui reçoit la liste des messages. |
| `hg.rest_chatbot(url, body_template, response_path, **options)` | Chatbot classique (dernier message + session). |
| `hg.function_chatbot(fn)` | Fonction Python. |
| `hg.command_chatbot(commande, input_mode="message", timeout=60)` | Programme en ligne de commande, sans shell. |
| `hg.demo_chatbot("flawed" ou "good")` | Faux chatbot pour essayer, sans clé. |
| `hg.Chatbot(adapter, system_prompt=None, name=…, description=…, intended_use=…, audience=…, languages=("fr",), tools=[…])` | Le chatbot **complet** : connexion + prompt + usage prévu + langues + outils. |

Options réseau communes (`**réseau`) : `timeout`, `max_retries`, `backoff`, `min_interval`.

## Le juge

| Fonction | Rôle |
|---|---|
| `hg.assisted_judge(profil_ou_adaptateur, *, rules=True, escalate_critical=True, **réseau)` | Un modèle juge, aidé par les règles ; les cas critiques qu'il écarte seul vont à un humain. À utiliser avec un modèle **différent** du chatbot audité. |

Sans `judge=`, `audit` utilise le juge intégré s'il est configuré, sinon les règles seules (avec un avertissement). Voir [le juge](juge.md).

## Le résultat : `Release`

| Attribut | Sens |
|---|---|
| `release.decision` | `"pass"`, `"review_required"`, `"blocked"` (ou `None` si l'audit n'a pas eu lieu) |
| `release.passed`, `release.ready`, `release.exit_code` | Booléens et code de sortie |
| `release.recommendations` | Liste de dictionnaires : `priority`, `title`, `observed`, `action`, `prompt_snippet`… |
| `release.report` | Le rapport complet (dictionnaire) : `results`, `stats`, `metadata`, `integrity` |
| `release.report["stats"]["interpretation"]` | La lecture en phrases de chaque pilier |
| `release.prompt_review` | Revue du prompt système |
| `release.scope` | Ce qui est couvert, non couvert, et les limites |
| `release.paths` | Fichiers écrits (`json`, `html`) |
| `release.summary(max_recommendations=5, detail=False)` | Texte : aperçu court, ou complet avec `detail=True` |

## Exceptions

`AuditFailed` : levée par `gate`. Son attribut `release` contient le rapport complet et les recommandations.

## Exemple : test d'intégration continue

```python
import healthy_guard as hg

def test_chatbot_publiable():
    bot = hg.openai_chatbot("mon-modele", base_url="https://api.exemple.com/v1", api_key_env="MA_CLE")
    hg.gate(bot, system_prompt=open("prompt.txt", encoding="utf-8").read(), limit=40, concurrency=4)
```
