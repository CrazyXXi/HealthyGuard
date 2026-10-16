# Connecter votre chatbot

HealthyGuard n'a besoin que d'une chose : pouvoir **envoyer une conversation et recevoir la réponse**. Choisissez votre cas.

| Votre chatbot | Python | Ligne de commande |
|---|---|---|
| Modèle derrière une API (OpenAI, Gemini, Mistral, vLLM…) | `hg.openai_chatbot(...)` | `--adapter openai` |
| Anthropic | `hg.anthropic_chatbot(...)` | `--adapter anthropic` |
| Modèle local (Ollama, LM Studio, llama.cpp) | `hg.local_chatbot("llama3.1")` | `--adapter openai --base-url http://localhost:11434/v1 --no-api-key` |
| Votre serveur HTTP (reçoit la liste des messages) | `hg.http_chatbot(url, ...)` | `--adapter http` |
| Chatbot classique (Rasa, Botpress, Dialogflow) | `hg.rest_chatbot(...)` | `--adapter rest` |
| Fonction Python | `hg.function_chatbot(fn)` | `--adapter python --callable fichier:fonction` |
| Programme en ligne de commande | `hg.command_chatbot([...])` | `--adapter command --command "..."` |
| Votre propre classe | sous-classe de `ChatbotAdapter` | `--adapter plugin --plugin module:Classe` |
| Interface web seulement, sans API | tableur à remplir | `export-scenarios` puis `--adapter answers` |

## Les clés d'API : jamais dans le code

On ne donne jamais la clé, seulement **le nom de la variable d'environnement** qui la contient. Les rapports ne contiennent aucun secret.

```python
bot = hg.openai_chatbot("gemini-3.1-flash-lite", base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                        api_key_env="GEMINI_API_KEY")
```
```powershell
$env:GEMINI_API_KEY = "votre_clé"          # PowerShell ; sous Linux/macOS : export GEMINI_API_KEY=...
```
Ou un fichier local `.env` (ligne `NOM=valeur`), chargé avec `--env-file .env`. Ajoutez `.env` à votre `.gitignore`. Si une clé a été collée dans un message,
un ticket ou un dépôt, considérez-la comme perdue et régénérez-la.

## Modèle derrière une API

```python
bot = hg.openai_chatbot("mon-modele", base_url="https://api.exemple.com/v1", api_key_env="MA_CLE",
                        temperature=0.0, max_tokens=1024, extra_body={"reasoning_effort": "low"})
```
Compatible avec tout serveur qui parle le format « chat/completions ». Les erreurs de saturation (503) et de quota (429) sont retentées avec attente progressive ;
`--min-interval` (secondes entre deux appels) aide à respecter un quota gratuit.

## Modèle local, sans clé

```python
bot = hg.local_chatbot("llama3.1")                                       # Ollama
bot = hg.local_chatbot("mon-modele", base_url="http://localhost:1234/v1")   # LM Studio
```
Aucune clé n'est envoyée aux adresses autres qu'OpenAI, sauf si vous en donnez une. Le premier appel peut être long (chargement du modèle).

## Chatbot classique (Rasa et autres)

Ces chatbots reçoivent **le dernier message et un identifiant de session**, pas la conversation entière. Décrivez le corps de la requête avec des
variables : `{{message}}` (dernier message), `{{session}}` (identifiant de session), `{{messages}}` (toute la conversation, pour les bots qui la réclament).

```python
bot = hg.rest_chatbot("http://localhost:5005/webhooks/rest/webhook",
                      body_template={"sender": "{{session}}", "message": "{{message}}"},
                      response_path="*.text")          # « * » concatène le texte de chaque élément de la liste
```
Le gabarit est rempli sur des objets : guillemets, retours à la ligne et accolades d'un message restent intacts. Une réponse vide (liste vide) compte comme un échec : le
chatbot s'est tu. Mettez les secrets dans les en-têtes (`--header-env NOM=VARIABLE`, ou `headers_env` dans un profil JSON), jamais dans le gabarit, qui est enregistré dans le rapport. Sous PowerShell, passez le gabarit
d'un fichier : `--body-template @gabarit.json`.

## Fonction Python

```python
def mon_chatbot(messages):                 # messages = [{"role": "user"|"assistant", "content": "..."}]
    return ma_logique(messages)            # un texte, ou un dictionnaire {"text": "..."}

bot = hg.function_chatbot(mon_chatbot)
```
Pratique pour envelopper n'importe quelle bibliothèque (LangChain, SDK d'un fournisseur…) : voir le notebook `healthy_guard/test.ipynb`.

## Programme en ligne de commande

```bash
healthy-guard run --adapter command --command "python mon_bot.py" --command-input message
```
Le message arrive sur l'entrée standard (`message`), en JSON (`json`) ou en argument (`arg`). Le programme est lancé sans shell ; la variable `HG_SESSION` contient
l'identifiant de la conversation.

## Mode manuel, sans API

Pour un chatbot que vous ne pouvez utiliser que dans une interface (ChatGPT, Gemini, widget d'un site) :

1. `healthy-guard export-scenarios --out scenarios.csv` : un tableur des messages à envoyer (environ 20 cas par pilier ; l'équité est exclue car elle exige tous les profils).
2. Pour **chaque numéro de conversation**, ouvrez un **nouveau** chat, envoyez les messages dans l'ordre des tours, collez chaque réponse dans la colonne `reponse_du_chatbot`.
   Les conversations `xxx#1` puis `xxx#2` se font dans deux chats différents (le second ne doit rien savoir du premier).
3. `healthy-guard run --adapter answers --answers scenarios.csv`.

Même juge, mêmes statistiques et recommandations. Le rapport signale les limites : réponses supposées fidèles, prompt non contrôlé, petit échantillon.

## Le prompt système et ce qui n'est pas visible

Pour une API de modèle, le prompt système que vous fournissez est **transmis à chaque question**. Pour un serveur, un chatbot classique, une fonction ou une commande, le prompt
vit de l'autre côté : HealthyGuard le **relit** (revue de texte) mais ne peut pas le transmettre, et teste le chatbot tel que votre serveur l'applique. Le rapport l'écrit
dans « Périmètre de l'audit ».

## Décrire votre chatbot dans un fichier

`healthy-guard init` écrit `healthyguard.json` (sans aucun secret : seulement des noms de variables). Ensuite, `healthy-guard run` suffit. Clés reconnues :
`chatbot`, `system_prompt_file`, `name`, `intended_use`, `audience`, `languages`, `tools`, `pillars`, `limit`, `repeats`, `concurrency`, `env_file`, `out_dir`, `max_major_fails`.
