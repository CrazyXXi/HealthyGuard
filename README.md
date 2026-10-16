# HealthyGuard

**Auditez votre chatbot de santé avant de le mettre en ligne.**

HealthyGuard pose des centaines de questions à votre chatbot (urgences, données personnelles, profils variés, pièges subtils), juge ses réponses et vous
rend une **décision** (publiable, à relire, bloqué), des **statistiques expliquées en phrases** et des **recommandations** prêtes à copier dans votre prompt.

- **En boîte noire** : on interroge le chatbot, on n'installe rien dedans et on ne s'interpose pas entre lui et ses utilisateurs.
- **Pour n'importe quel chatbot** : API d'un modèle (OpenAI, Gemini, Mistral…), modèle local (Ollama, LM Studio), serveur maison, Rasa, fonction Python,
  programme en ligne de commande, ou même simple interface web (mode manuel, sans API).
- **Sans rien configurer côté juge** : un juge intégré lit les réponses. Vous n'avez ni modèle ni jeton à fournir pour l'audit.

## Bon à savoir avant de commencer

- **Le juge intégré est un service en ligne.** Pour juger les réponses de votre chatbot, HealthyGuard envoie au service les **conversations de l'audit** : les scénarios de test
  (écrits par HealthyGuard) et les **réponses de votre chatbot**. Il n'envoie ni vos clés, ni votre prompt système (sauf si vous le demandez avec `--prompt-review-llm`), et le
  service n'enregistre aucun contenu. Si les réponses de votre chatbot peuvent contenir des données personnelles ou de santé **réelles**, n'utilisez pas le juge intégré :
  choisissez [votre propre juge](docs/juge.md), qui reste chez vous.
- **Ce service est temporaire.** Il est prévu pour quelques semaines. S'il s'arrête, l'audit continue avec des **règles seules** (gratuit, sans réseau, mais approximatif) et
  l'affiche clairement. Vous pouvez toujours brancher votre propre juge.
- **Version expérimentale (0.1).** Le juge n'a pas été calibré sur de nombreuses annotations humaines et les scénarios n'ont pas été relus par un professionnel de santé.
  C'est un outil d'aide à la décision, pas une certification.

## Installer

```bash
pip install git+https://github.com/CrazyXXi/HealthyGuard.git
```

ou, depuis un clone du dépôt : `pip install -e .` (Python 3.10 ou plus, aucune dépendance).

## Essayer en 30 secondes (sans clé, sans chatbot)

```python
import healthy_guard as hg

release = hg.audit(hg.demo_chatbot("flawed"), limit=8)
```

ou en ligne de commande : `healthy-guard run --adapter demo-flawed --limit 8`. Vous verrez la décision, un niveau par pilier, les actions prioritaires et
l'emplacement du rapport HTML. D'autres exemples prêts à copier sont dans [examples/](examples/).

## Auditer votre chatbot

```python
import healthy_guard as hg

bot = hg.openai_chatbot("gemini-3.1-flash-lite", base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                        api_key_env="GEMINI_API_KEY")                        # le NOM de la variable qui contient votre clé

release = hg.audit(bot, system_prompt=open("prompt_systeme.txt", encoding="utf-8").read(),
                   name="Mon assistant santé", intended_use="Informations générales de santé", concurrency=4)

print(release.decision)       # "pass", "review_required" ou "blocked"
```

Pas de code ? `healthy-guard init` pose quelques questions et écrit `healthyguard.json`, puis `healthy-guard run` fait tout l'audit.

Dans vos tests ou votre intégration continue : `hg.gate(bot, system_prompt=...)` lève `AuditFailed` si le chatbot ne peut pas être publié.

## Ce que l'audit vérifie

| Pilier | Question |
|---|---|
| Sécurité clinique | Oriente-t-il vers les secours en cas d'urgence, sans rassurer à tort ni diagnostiquer ? |
| Transparence | Dit-il qu'il est une IA, reconnaît-il ses limites, évite-t-il de se faire passer pour un soignant ? |
| Équité | Répond-il aussi bien à tous les profils (âge, sexe, origine, handicap, poids, situation sociale) ? |
| Confidentialité | Évite-t-il de recopier des identifiants, de fuiter des données, de promettre un secret impossible ? |
| Information et consentement | Informe-t-il sur l'usage des données et respecte-t-il les choix (refus, suppression, mineurs) ? |
| Traçabilité | L'audit est-il rejouable et vérifiable (configuration, preuves, empreinte d'intégrité) ? |

Le chatbot reçoit aussi une **revue de son prompt système** (garde-fous présents ou absents, consignes dangereuses).

## Ce que vous obtenez

À l'écran, un aperçu court :

```
== Mon assistant santé : PUBLICATION BLOQUÉE ==

  Sécurité clinique              satisfaisant
  Confidentialité                préoccupant
  ...
À faire en priorité :
  1. [CRITIQUE] Ne pas promettre ce qu'on ne peut pas garantir sur les données
Détail complet : reports/audit.html
```

Dans le rapport HTML et JSON : une lecture en phrases de chaque pilier (niveau, fiabilité du chiffre, points faibles), les statistiques avec intervalles de
confiance, les écarts entre profils, les recommandations avec un paragraphe de prompt à copier, le périmètre couvert et non couvert, et un fichier signé
(SHA-256) pour vérifier que le rapport n'a pas été modifié. Voir [lire les résultats](docs/lire-les-resultats.md).

## Documentation

| Guide | Contenu |
|---|---|
| [Démarrage](docs/demarrage.md) | Installer, premier audit, comprendre la décision |
| [Connecter votre chatbot](docs/connecter-votre-chatbot.md) | API, modèle local, serveur maison, Rasa, fonction, commande, mode manuel sans API |
| [Piliers et scénarios](docs/piliers-et-scenarios.md) | Ce qui est testé, comment sont écrits les scénarios, ajouter les vôtres |
| [Lire les résultats](docs/lire-les-resultats.md) | Statuts, décision, statistiques, niveaux, fiabilité |
| [Le juge](docs/juge.md) | Comment les réponses sont jugées, relecture humaine, juge personnalisé |
| [Héberger le juge intégré](docs/heberger-le-juge.md) | Pour les mainteneurs : déployer la passerelle, sécurité des identifiants |
| [Référence ligne de commande](docs/reference-cli.md) | Toutes les commandes |
| [Référence Python](docs/reference-api.md) | Toutes les fonctions de `healthy_guard` |
| [Limites et questions fréquentes](docs/limites-et-faq.md) | Ce que l'outil ne fait pas, erreurs courantes |
| [Référence avancée](docs/avance.md) | Jurys, calibration, comparaison de versions, audit par morceaux |

## Limites à connaître

HealthyGuard observe des **réponses**. Il ne voit ni votre base de connaissances, ni la conservation des données, ni la sécurité de votre infrastructure, et il
ne remplace ni une relecture par un professionnel de santé, ni une analyse de conformité (RGPD, dispositif médical). Les scénarios sont écrits en français.
Un audit sur peu de cas donne des conclusions fragiles : il en faut au moins 30 par pilier pour un résultat solide. Détails : [limites et FAQ](docs/limites-et-faq.md).

## Licence

MIT, voir [LICENSE](LICENSE).
