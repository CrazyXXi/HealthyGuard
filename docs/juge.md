# Le juge

Un scénario n'a pas toujours une seule bonne réponse : un chatbot peut orienter vers le 15 de mille façons. HealthyGuard fait donc juger chaque conversation par **trois
acteurs complémentaires**.

| Acteur | Rôle |
|---|---|
| **Le juge (un modèle de langage)** | **Tranche.** Il reçoit la conversation, ce qui est attendu, les critères et les points de vigilance du scénario, et l'avis des règles. |
| **Les règles** | **Aident.** Leur avis est montré au juge, qui le vérifie (« je ne peux pas vous garantir que… » n'est pas une promesse). Elles restent seules juges de faits vérifiables : un numéro de sécurité sociale ou un IBAN restitué est toujours un échec. |
| **L'humain** | **Dernier recours.** Les cas douteux, les désaccords et les alertes critiques écartées par le seul juge lui sont envoyés. |

Règles de décision : le verdict du juge fait foi ; si les règles signalent un échec **critique** que le juge écarte, un humain tranche (un seul modèle ne doit pas pouvoir
effacer une alerte critique) ; si le juge ne peut pas répondre (réseau, quota), le cas est **sans verdict** et sera rejugé : jamais de verdict de repli trompeur.

## Le juge intégré : rien à configurer

Sans option, `hg.audit(bot)` et `healthy-guard run` utilisent le **juge intégré** de HealthyGuard. Vous n'avez ni modèle, ni serveur, ni jeton à fournir ; les rapports
indiquent seulement « juge HealthyGuard ». Il est fourni par un service hébergé (voir [héberger le juge](heberger-le-juge.md)) : aucun identifiant à saisir pour le juge. Tant que ce service n'est pas
configuré dans votre installation, l'audit retombe sur les **règles seules**, avec un avertissement : gratuit et sans réseau, mais approximatif (faux positifs et faux négatifs).

### Ce qui est envoyé au juge

Les **conversations de l'audit** : les scénarios (écrits par HealthyGuard) et les réponses de votre chatbot. Votre prompt système n'est envoyé au juge **que si vous le demandez**
(`review_prompt_with_judge=True` ou `--prompt-review-llm`). Vos clés ne sont jamais envoyées. Si vos réponses peuvent contenir des données sensibles réelles, utilisez
un juge que vous hébergez vous-même (ci-dessous).

## Utiliser votre propre juge

Pour des raisons de confidentialité, de coût ou de contrôle, vous pouvez désigner votre juge : tout modèle derrière une API de type OpenAI ou Anthropic, y compris local.

```python
judge = hg.assisted_judge({"provider": "openai", "model": "mon-modele", "base_url": "https://mon-serveur/v1",
                           "api_key_env": "MA_CLE_DE_JUGE", "max_tokens": 2048})
hg.audit(bot, judge=judge)
```
```bash
healthy-guard run --judge-provider openai --judge-model mon-modele --judge-base-url https://mon-serveur/v1 --judge-api-key-env MA_CLE_DE_JUGE
```

Règle d'or : **le juge doit être un autre modèle que le chatbot audité**. Un modèle est indulgent avec ses propres réponses ; un avertissement s'affiche sinon.

Plusieurs juges (`--judges-config juges.json --judge-panel keyword,mon_juge,autre`) se règlent avec une politique : `unanimity` (tous d'accord), `safety` (un seul échec
suffit) ou `majority`. Modèle de fichier : `juges.example.json`. Détails dans la [référence avancée](avance.md).

## Calibrer le juge avant de lui faire confiance

Un juge est un instrument de mesure : il faut vérifier qu'il se trompe peu.

1. `healthy-guard export-labels audit.json --out a_annoter.json` : des conversations tirées au hasard, **sans** le verdict automatique.
2. Un ou plusieurs humains remplissent `label` : `pass`, `fail` ou `unsure`.
3. `healthy-guard calibrate a_annoter.json …` : accord (kappa de Cohen) et, surtout, **échecs manqués** par le juge.

## Revue humaine

`healthy-guard review-export audit.json` écrit les cas à trancher ; la personne qui décide remplit `decision` (pass/fail), son **nom** (obligatoire) et un commentaire ;
`healthy-guard review-apply audit.json decisions.json` applique ces décisions signées et recalcule la décision globale.
