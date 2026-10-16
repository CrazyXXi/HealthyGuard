# Héberger le juge intégré (pour les mainteneurs)

Cette page s'adresse à la personne qui publie HealthyGuard, pas à ses utilisateurs.

## Le principe : une passerelle, pas des identifiants dans le package

Le juge intégré est un modèle hébergé sur un serveur protégé par une clé et un secret. **On ne peut pas mettre cette clé dans le package.** Un package installé par pip,
ou un dépôt public, se lit : n'importe qui copierait la clé, l'utiliserait à vos frais, puis vous seriez obligé de la révoquer — et tous les utilisateurs seraient en
panne. On ne peut pas non plus la « cacher » dans le code : elle est extraite en quelques minutes.

La solution est de **déployer un petit serveur intermédiaire (une passerelle)** :

```
chatbot de l'utilisateur  ──>  HealthyGuard (package, sans secret)  ──>  passerelle (vos identifiants)  ──>  modèle hébergé
```

- Le package ne contient que l'**adresse publique de la passerelle** (aucun secret) et le nom d'un modèle fictif, `healthyguard-judge`.
- La passerelle ajoute la clé et le secret côté serveur, impose le vrai modèle, limite le débit, plafonne la taille des requêtes et **n'enregistre aucun contenu**.
- Le vrai nom du modèle, l'adresse du serveur d'inférence et les identifiants ne sortent jamais de la passerelle. Les rapports indiquent seulement `healthyguard-judge`.
- Changer de modèle, de serveur ou de clé ne demande aucune mise à jour du package.

Le code est dans [`server/judge_gateway.py`](../server/judge_gateway.py).

## Déployer (une fois)

```bash
pip install modal
modal secret create healthyguard-judge UPSTREAM_URL=https://<adresse du modèle> UPSTREAM_TOKEN=<clé>.<secret> UPSTREAM_MODEL=<nom du modèle>
modal deploy server/judge_gateway.py
```

Modal affiche l'adresse publique de la passerelle. Test : `GET <adresse>/health` répond `{"status": "ok"}`. La passerelle peut tourner ailleurs que sur Modal (n'importe quel
hébergeur Python) : seule la fonction `gateway()` compte.

Ensuite, deux façons d'indiquer l'adresse aux utilisateurs :

1. **Dans le package** : renseignez `HOSTED_JUDGE_URL` dans `healthy_guard/api.py`, republiez. Tous les utilisateurs l'ont sans rien faire.
2. **Par variable d'environnement** : `HEALTHYGUARD_JUDGE_URL=<adresse>` (utile pour une équipe, ou pour tester).

Si vous voulez exiger une clé d'accès (pour savoir qui utilise le service et pouvoir en couper l'accès), ajoutez `ALLOWED_KEYS=cle1,cle2` dans le secret ; les utilisateurs la
donnent avec `HEALTHYGUARD_API_KEY`. Cette clé est la vôtre à distribuer et à révoquer : ce n'est pas celle du modèle.

## Ce qu'il faut décider

| Sujet | Recommandation |
|---|---|
| **Coût** | Chaque audit complet fait plusieurs centaines d'appels. Le débit par adresse IP est plafonné (60/minute) ; ajoutez des clés d'accès si l'usage grandit. |
| **Disponibilité** | Un serveur « à la demande » démarre à froid (premières secondes plus longues). `min_containers=1` le garde chaud, contre un coût permanent. Les audits retentent automatiquement les erreurs temporaires. |
| **Données** | Le juge reçoit des conversations de test et les réponses du chatbot audité. Écrivez-le dans votre politique de confidentialité, gardez `aucun contenu enregistré`, et proposez aux utilisateurs sensibles de [choisir leur propre juge](juge.md). |
| **Durée de vie** | Prévenez vos utilisateurs si le service est temporaire : sans lui, l'audit retombe sur les règles seules avec un avertissement, mais ne s'arrête pas. |
| **Secrets** | Ne jamais committer la clé ni le secret. Si ils ont été collés dans un message ou un dépôt, les régénérer. `juges.json` (vos profils locaux) est ignoré par git. |

## Vérifier avant de publier

1. `python -m pytest` : les tests ne font aucun appel réseau.
2. `HEALTHYGUARD_JUDGE_URL=<adresse> python examples/01_essai_sans_cle.py` : l'audit de démonstration doit afficher une décision sans avertissement « règles seules ».
3. Vérifier qu'aucun nom de modèle ni adresse d'inférence n'apparaît dans le rapport HTML ni dans `git grep`.
