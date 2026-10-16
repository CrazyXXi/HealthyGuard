# Démarrage

## 1. Installer

```bash
pip install git+https://github.com/CrazyXXi/HealthyGuard.git
```

Depuis un clone du dépôt : `pip install -e .` (le mode « éditable » prend en compte vos modifications sans réinstaller). Python 3.10 ou plus, aucune
dépendance externe. Vérification : `healthy-guard --help`.

## 2. Premier essai, sans chatbot et sans clé

```python
import healthy_guard as hg
release = hg.audit(hg.demo_chatbot("flawed"), limit=8)
```

`demo_chatbot("flawed")` est un faux chatbot qui a des défauts volontaires ; `"good"` n'en a pas. Cet essai ne fait aucun appel réseau si aucun juge intégré
n'est configuré (le jugement se fait alors par règles, avec un avertissement).

## 3. Auditer votre chatbot

Dites à HealthyGuard **comment on parle à votre chatbot** (voir [Connecter votre chatbot](connecter-votre-chatbot.md)), donnez son **prompt système** si vous
l'avez, puis lancez l'audit :

```python
release = hg.audit(bot, system_prompt=PROMPT, name="Mon assistant", intended_use="Informations générales de santé",
                   audience="grand public", languages=("fr",), concurrency=4)
```

| Paramètre | Rôle |
|---|---|
| `system_prompt` / `system_prompt_file` | Le prompt de production : il est relu (garde-fous présents ou absents) et transmis à chaque question quand la connexion le permet. |
| `name`, `intended_use`, `audience`, `languages`, `tools` | La fiche de votre chatbot, écrite dans le rapport. L'usage prévu et le public aident à juger la gravité des défauts. |
| `limit` | N scénarios par pilier : audit rapide. **Enlevez-le pour l'audit complet.** |
| `repeats` | Rejouer chaque cas N fois pour mesurer la variance. |
| `concurrency` | N scénarios en même temps (baissez si votre serveur renvoie des erreurs 429). |
| `pillars` | Limiter à certains piliers (la traçabilité est toujours ajoutée). |
| `output_dir`, `name` | Où écrire `audit.json` et `audit.html`. |

En ligne de commande : `healthy-guard init` (assistant qui écrit `healthyguard.json`), puis `healthy-guard run`. Pour vérifier un branchement avant un long audit :
`healthy-guard check-bot` (le chatbot répond-il ? garde-t-il le fil ? isole-t-il les conversations ?).

## 4. Comprendre le résultat

`release.decision` vaut :

| Décision | Sens |
|---|---|
| `pass` | Aucun échec bloquant sur les cas testés. Ne dispense pas d'une relecture par un professionnel de santé. |
| `review_required` | Des cas doivent être tranchés par un humain avant publication. |
| `blocked` | Au moins un échec critique, ou trop d'échecs majeurs (3 par défaut, réglable avec `max_major_fails`). Corrigez puis relancez. |

Autres attributs utiles : `release.recommendations` (les actions, classées par urgence), `release.report` (tout le rapport, sous forme de dictionnaire),
`release.paths` (les fichiers écrits), `release.ready` (faux si le chatbot n'était pas joignable : l'audit n'a pas eu lieu).

L'aperçu à l'écran est court. Pour tout voir : `release.summary(detail=True)`, ou `--details` en ligne de commande, ou ouvrez le rapport HTML.

## 5. Corriger puis recommencer

1. Lisez les recommandations : chacune dit ce qui a été observé, avec des extraits, et propose un paragraphe à ajouter à votre prompt système.
2. Corrigez le prompt ou le code, relancez l'audit.
3. Comparez les deux rapports pour vérifier qu'aucune régression n'est apparue : `healthy-guard compare ancien.json nouveau.json`.

## 6. L'intégrer à votre routine

```python
hg.gate(bot, system_prompt=PROMPT)      # dans un test : lève AuditFailed si la décision n'est pas « pass »
```

En ligne de commande, le code de sortie sert aux scripts et à l'intégration continue : 0 = publiable, 1 = relecture requise, 2 = bloqué,
64 = configuration invalide ou chatbot injoignable.

## Combien de temps ça prend ?

Chaque scénario demande un appel à votre chatbot et un au juge. Avec `limit=8` (environ 45 cas), comptez quelques minutes ; l'audit complet (plusieurs
centaines de scénarios) peut prendre une à plusieurs heures selon la vitesse et les quotas de votre chatbot. `concurrency` accélère, `--min-interval`
(ligne de commande) ralentit volontairement pour respecter un quota.
