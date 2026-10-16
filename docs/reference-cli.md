# Référence de la ligne de commande

`healthy-guard <commande> [options]` (ou `python -m healthy_guard ...`). Aide de chaque commande : `healthy-guard <commande> --help`.

## Codes de sortie

| Code | Sens |
|---|---|
| 0 | Publication possible |
| 1 | Relecture humaine requise |
| 2 | Publication bloquée |
| 64 | Configuration invalide, chatbot injoignable ou audit non effectué |

## Les commandes

| Commande | Usage |
|---|---|
| `init` | Assistant qui écrit `healthyguard.json` (la description de votre chatbot, sans secret). `--file`, `--force`. |
| `run` | **Parcours complet avant publication** : connexion, revue du prompt, audit, recommandations, rapports. Lit `healthyguard.json`. |
| `audit` | Audit seul, avec rapport JSON (`--out`) et HTML (`--html`). Mêmes options de chatbot et de juge que `run`. |
| `check-bot` | Diagnostic du branchement avant un long audit : réponse, mémoire de la conversation, isolation, caractères spéciaux. |
| `recommend rapport.json` | Recommandations et revue du prompt à partir d'un rapport existant (`--system-file`, `--top`, `--out`, `--html`). |
| `report rapport.json --html x.html` | Régénérer le rapport HTML. |
| `verify rapport.json` | Vérifier qu'un rapport n'a pas été modifié (empreinte SHA-256). |
| `compare a.json b.json` | Comparer deux versions du chatbot : régressions, améliorations (test de McNemar exact). |
| `merge a.json b.json … --out` | Fusionner des audits d'un même chatbot faits par morceaux (le premier rapport a priorité). |
| `rejudge rapport.json` | Refaire juger les conversations d'un rapport **sans rappeler le chatbot** (autre juge, autre critère). |
| `review-export` / `review-apply` | Exporter les cas à trancher par un humain, puis appliquer les décisions signées (nom obligatoire) et recalculer la décision. |
| `export-labels` / `calibrate` | Faire annoter des conversations en aveugle par des humains, puis mesurer l'accord du juge (kappa, échecs manqués). |
| `export-scenarios` | **Mode sans API** : tableur des messages à passer à la main (`--out`, `--pillars`, `--limit`). Puis `--adapter answers --answers fichier.csv`. |

## Options du chatbot (audit, run, check-bot)

| Option | Rôle |
|---|---|
| `--adapter` | `openai`, `anthropic`, `http`, `rest`, `python`, `command`, `plugin`, `answers`, ou `demo-good` / `demo-flawed` / `mock-bad` (sans réseau) |
| `--model`, `--base-url`, `--api-key-env`, `--no-api-key` | Modèle derrière une API ; `--no-api-key` pour un serveur sans authentification |
| `--system`, `--system-file` | Prompt système du chatbot |
| `--temperature`, `--max-tokens`, `--extra-body` | Paramètres d'appel (`--extra-body '{"…": …}'` ou `@fichier.json`) |
| `--url`, `--request-field`, `--response-path`, `--header`, `--header-env` | Serveur HTTP (adaptateurs `http` et `rest`) |
| `--body-template`, `--method`, `--body-format` | Chatbot classique : `{{message}}`, `{{session}}`, `{{messages}}` |
| `--callable`, `--command`, `--command-input`, `--plugin`, `--plugin-args` | Fonction, programme ou classe personnalisée |
| `--chatbot-config`, `--chatbot` | Profils de chatbots dans un fichier JSON |
| `--answers` | Tableur de réponses collées à la main |
| `--chatbot-name`, `--intended-use`, `--audience`, `--languages` | La fiche du chatbot, écrite dans le rapport |

## Options de l'audit

| Option | Rôle |
|---|---|
| `--config healthyguard.json` | Fichier de description (défaut pour `run`) |
| `--pillars` | `clinical_safety,transparency,equity,privacy,consent` |
| `--limit N`, `--repeats N` | Échantillon par pilier, rejeux |
| `--concurrency N` | N scénarios en même temps |
| `--min-interval S`, `--max-retries N`, `--backoff S`, `--timeout S` | Respect des quotas, reprises sur 429/503, délais |
| `--max-major-fails N` | Seuil de blocage des échecs majeurs (défaut : 3) |
| `--extra-cases fichier.json` | Vos propres scénarios |
| `--retry-errors rapport.json` | Ne relancer que les cas restés sans réponse |
| `--env-file .env` | Charger des variables (clés) depuis un fichier local |
| `--skip-check`, `--no-prompt-review`, `--prompt-review-llm` | Désactiver le diagnostic ou la revue du prompt ; faire aussi relire le prompt par le juge |
| `--out-dir`, `--report-name` | Où et sous quel nom écrire les rapports (`run`) |
| `--details`, `--quiet` | Afficher tout le détail ; masquer la progression |

## Options du juge

Sans option, le juge intégré est utilisé quand il est configuré (voir [le juge](juge.md)). Pour en choisir un autre :

| Option | Rôle |
|---|---|
| `--judge keyword` | Règles seules (gratuit, approximatif) |
| `--judge-provider`, `--judge-model`, `--judge-base-url`, `--judge-api-key-env`, `--judge-extra-body` | Un juge décrit en ligne |
| `--judges-config juges.json`, `--judge-profile nom` | Un juge décrit dans un fichier (modèle : `juges.example.json`) |
| `--judge-panel a,b,c`, `--panel-policy {unanimity,safety,majority}`, `--judge-votes N` | Plusieurs juges |
| `--no-rule-hints` | Ne pas montrer l'avis des règles au juge |
