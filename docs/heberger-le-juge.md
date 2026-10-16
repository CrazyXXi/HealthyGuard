# Héberger le juge intégré (pour les mainteneurs)

Cette page s'adresse à la personne qui publie HealthyGuard, pas à ses utilisateurs.

## Le principe : une passerelle, pas des identifiants dans le package

Le juge intégré est un modèle hébergé sur un serveur protégé par une clé et un secret. **On ne peut pas mettre cette clé dans le package.** Un package installé par pip, ou un
dépôt public, se lit : n'importe qui copierait la clé, l'utiliserait à vos frais, puis vous seriez obligé de la révoquer, et tous les utilisateurs seraient en panne. On ne peut
pas non plus la « cacher » dans le code : elle est extraite en quelques minutes.

La solution est de **déployer un petit serveur intermédiaire (une passerelle)** :

```
chatbot de l'utilisateur ──> HealthyGuard (package, sans secret) ──> passerelle (vos identifiants) ──> modèle hébergé
```

- Le package ne contient que l'**adresse publique de la passerelle** (aucun secret) et un nom de modèle fictif, `healthyguard-judge`.
- La passerelle ajoute la clé et le secret côté serveur, impose le vrai modèle, limite le débit, plafonne la taille des requêtes et **n'enregistre aucun contenu**.
- Le vrai nom du modèle, l'adresse du serveur d'inférence et les identifiants ne sortent jamais de la passerelle.
- Changer de modèle, de serveur ou de clé ne demande aucune mise à jour du package.
- **Pour les utilisateurs : aucun identifiant à saisir pour le juge.** Ils n'ont à fournir que l'accès à leur propre chatbot.

Le code est dans [`server/`](../server/) : `app.py` (l'application), `api/index.py` (entrée Vercel), `vercel.json`, `requirements.txt`, `Dockerfile`, `.env.example`.

## Déployer sur Vercel

1. Poussez le dépôt sur GitHub (c'est déjà fait), puis sur vercel.com : **Add New → Project**, choisissez le dépôt.
2. **Root Directory : `server`** (important : c'est le dossier qui contient `vercel.json`). Framework : « Other ».
3. **Settings → Environment Variables**, ajoutez (voir `server/.env.example`) :

   | Variable | Valeur |
   |---|---|
   | `UPSTREAM_URL` | adresse du modèle hébergé, sans `/v1` |
   | `UPSTREAM_TOKEN` | `identifiant.secret` du modèle |
   | `UPSTREAM_MODEL` | nom exact du modèle chez le serveur d'inférence |
   | `ALLOWED_KEYS` | (facultatif) clés d'accès à distribuer, séparées par des virgules |
   | `RATE_PER_MINUTE` | (facultatif) défaut 60 par adresse IP |

4. **Deploy.** Test : `https://<votre-projet>.vercel.app/health` doit répondre `{"status": "ok"}`.
5. Mettez l'adresse (`https://<votre-projet>.vercel.app`) dans `HOSTED_JUDGE_URL` de `healthy_guard/api.py`, puis republiez le package. Pour essayer avant de republier :
   `HEALTHYGUARD_JUDGE_URL=https://<votre-projet>.vercel.app python examples/01_essai_sans_cle.py`.

Le même dossier fonctionne ailleurs : `docker build -t hg-gateway server && docker run -p 8000:8000 --env-file server/.env hg-gateway` (Render, Railway, Fly, un serveur à vous),
ou en local `cd server && uvicorn app:app --port 8000`.

## Points d'attention sur Vercel

| Sujet | À savoir |
|---|---|
| **Durée** | Une fonction Vercel a une durée maximale (60 s configurée dans `vercel.json`, selon votre offre). Le juge utilise un raisonnement faible (`REASONING_EFFORT=low`) pour répondre vite ; au-delà, l'appel échoue en 503 et l'audit le retente. |
| **Limite de débit** | Le compteur est en mémoire : sur Vercel, chaque instance a le sien, donc la limite est **indicative**. Pour une vraie protection, activez les règles de débit du pare-feu Vercel, et/ou exigez des clés (`ALLOWED_KEYS`). |
| **Coût** | Chaque audit complet fait plusieurs centaines d'appels. Sans clé d'accès, tout le monde peut utiliser votre quota : surveillez-le, ou distribuez des clés. |
| **Données** | Le juge reçoit des conversations de test et les réponses du chatbot audité. La passerelle ne les enregistre pas ; n'activez pas de journalisation du corps des requêtes chez l'hébergeur. Écrivez-le dans votre politique de confidentialité. |
| **Clés d'accès** | Avec `ALLOWED_KEYS`, vos utilisateurs doivent définir `HEALTHYGUARD_API_KEY` : c'est une clé **à vous** (distribuable et révocable), pas celle du modèle. |
| **Durée de vie** | Si le service s'arrête, l'audit retombe sur les règles seules avec un avertissement : il ne plante pas. |
| **Secrets** | Ne jamais committer la clé ni le secret. S'ils ont été collés dans un message, un ticket ou un dépôt, les régénérer. |

## Vérifier avant de publier

1. `python -m pytest` : aucun appel réseau (les tests de la passerelle se lancent si `fastapi`, `httpx` et `uvicorn` sont installés).
2. Après déploiement : l'audit de démonstration avec `HEALTHYGUARD_JUDGE_URL` ne doit plus afficher « règles seules ».
3. Aucun nom de modèle ni adresse d'inférence dans le rapport HTML ni dans `git grep` (un test le vérifie).
