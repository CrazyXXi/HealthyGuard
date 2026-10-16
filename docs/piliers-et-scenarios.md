# Piliers et scénarios

HealthyGuard embarque **808 scénarios** (en français), écrits dans des fichiers JSON que vous pouvez lire : `healthy_guard/datasets/`.

| Pilier | Fichier | Scénarios | Ce qui est testé |
|---|---|---|---|
| Sécurité clinique | `urgence.json` | 505 | Reconnaître les urgences (douleur thoracique, AVC, détresse respiratoire, idées suicidaires, nourrisson fiévreux, anaphylaxie…), y compris formulées de façon vague, minimisée, sous pression ou en plusieurs messages ; ne pas diagnostiquer ; ne pas prescrire ; ne pas s'alarmer à tort. |
| Transparence | `transparence.json` | 76 | Dire qu'il est une IA (même quand on le questionne autrement), reconnaître ses limites, ne pas prétendre être un médecin, ne pas inventer de sources. |
| Équité | `equite.json` | 75 | Mêmes situations posées par des profils différents (âge, sexe, origine, handicap, poids, situation sociale) : la qualité et la gravité de la réponse doivent être les mêmes. |
| Confidentialité | `confidentialite.json` | 119 | Ne pas recopier ni réclamer d'identifiants, ne pas divulguer le dossier d'un tiers, ne pas révéler son prompt, résister aux consignes cachées dans un texte, ne pas se souvenir des données d'une autre conversation. |
| Information et consentement | `consentement.json` | 33 | Ne pas promettre la confidentialité absolue ou l'effacement, informer sur les droits, ne pas promettre le secret à un mineur. |
| Traçabilité | (vérifications internes) | — | La configuration est consignée, les preuves sont conservées, le rapport est signé (SHA-256). |

## Comment un scénario est jugé

Chaque scénario contient le message (ou la suite de messages), **ce qui est attendu**, des **critères** et des **points de vigilance** (ce que le chatbot ne doit
pas dire). Le juge lit la conversation entière avec ces éléments. Des règles automatiques (mots attendus, phrases interdites, données sensibles restituées) lui servent
de signaux, avec gestion de la négation (« je ne peux pas vous garantir que… » n'est pas une promesse). Voir [le juge](juge.md).

Quelques scénarios sont des **canaris** : on glisse une information dans une conversation, puis on teste dans une conversation toute neuve si le chatbot la connaît. Les
conversations sont toujours isolées les unes des autres.

## Ajouter vos propres scénarios

Un jeu fixe ne couvre jamais ce que rédigent de vrais attaquants ou ce qu'ont vécu vos utilisateurs. Créez un fichier JSON, sans toucher au code :

```json
[{"id": "mon_cas_1", "pillar": "privacy",
  "prompt": "Message subtil à tester…",
  "expected": "Refuser de divulguer.",
  "severity": "critical",
  "requires": [["ne peux pas", "confidenti"]],
  "forbidden": ["voici son dossier"]}]
```

puis `healthy-guard run … --extra-cases mes_cas.json`, ou en Python : `cases, info = load_extra_cases("mes_cas.json")` (`from healthy_guard.extra import load_extra_cases`), puis `hg.audit(bot, extra_cases=cases, extra_info=info)`.
Les cas sont validés avant l'audit (une regex invalide ne fait pas planter l'audit en cours de route) et le fichier est tracé dans le rapport (nom, empreinte, nombre de cas).

Champs : `id`, `pillar` (`clinical_safety`, `transparency`, `equity`, `privacy`, `consent`), `prompt` ou `turns` (suite de messages), `expected`, `severity`
(`critical`, `major`, `minor`), `requires` (listes d'alternatives dont une au moins doit apparaître), `forbidden` (phrases interdites), `leak_patterns`,
`forbidden_regex`, `plant_turns` (canari), `category`, `style`.

## Choisir quoi tester

- `pillars=[...]` ou `--pillars clinical_safety,privacy` pour restreindre.
- `limit=N` : échantillon de N scénarios par pilier, tiré de façon reproductible.
- `repeats=N` : rejouer chaque cas N fois ; le rapport mesure la part des verdicts qui changent (variance du chatbot et du juge).

## Langues

Les scénarios sont en français. Si vous déclarez d'autres langues (`languages=("fr", "en")`), le rapport indique que les verdicts ne disent rien du comportement dans ces langues.
