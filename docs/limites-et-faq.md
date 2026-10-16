# Limites et questions fréquentes

## Ce que HealthyGuard ne fait pas

- **Il ne voit que des réponses** (boîte noire). Pas votre base de connaissances, pas les filtres situés hors du modèle, pas la conservation des données, pas la sécurité de votre
  hébergement : la conformité RGPD et dispositif médical se vérifie ailleurs.
- **Il ne remplace pas un professionnel de santé.** La qualité clinique détaillée des conseils demande une relecture humaine. Les scénarios d'urgence ont été rédigés pour
  tester l'orientation vers les secours, pas pour valider un protocole médical.
- **Il ne prouve pas l'absence de défaut.** Un audit réussi dit « aucun défaut observé sur ces scénarios », pas « il n'y en a pas ». Les attaquants inventent d'autres formulations :
  ajoutez vos propres scénarios.
- **Il est en français.** Les scénarios sont écrits en français ; le comportement dans d'autres langues n'est pas évalué.
- **Il ne surveille pas le chatbot en production.** Il intervient **avant** la publication ; relancez-le à chaque changement (prompt, modèle, base de connaissances).
- **Les outils** (prise de rendez-vous, envoi de messages) ne sont pas testés : un chatbot qui déclenche des actions demande des tests dédiés.

## Combien de scénarios faut-il ?

Au moins **30 par pilier** pour une conclusion solide, davantage pour l'équité (plusieurs cas par profil). `limit=8` sert aux essais. Un rapport sur peu de cas l'écrit
(« fiabilité du chiffre : faible »).

## Questions fréquentes

**« Aucune clé trouvée » ou erreur 400/401/403 du chatbot.** La variable d'environnement donnée dans `api_key_env` est vide ou mal copiée (espace, guillemets). Dans PowerShell :
`$env:MA_CLE = "..."`. Vérifiez avec `healthy-guard check-bot`.

**Erreur 429 (quota).** Ralentissez : `--min-interval 2`, baissez `concurrency`. Les cas restés sans réponse se relancent sans tout refaire : `--retry-errors rapport.json`.

**Erreur 503 (« high demand »).** Saturation passagère du service : l'audit réessaie avec une attente croissante. Si cela dure, relancez plus tard.

**Mon chatbot ne garde pas le fil.** `check-bot` l'avertit : soit il n'a pas de mémoire (normal pour certains), soit la session est mal transmise. Les scénarios à plusieurs messages sont alors moins significatifs.

**Un cas est « sans verdict ».** Le chatbot ou le juge n'a pas répondu (erreur technique) : ce n'est ni un échec ni une réussite. Relancez avec `--retry-errors`.

**« Jugement par règles seules ».** Le juge intégré n'est pas configuré. Les règles sont gratuites mais approximatives. Voir [le juge](juge.md).

**Je ne peux pas utiliser d'API, seulement une interface web.** Mode manuel : [connecter votre chatbot](connecter-votre-chatbot.md).

**Un faux positif : le juge a tort.** Les relectures humaines (`review-export` / `review-apply`) corrigent les cas, et `calibrate` mesure la fiabilité du juge. Signalez les scénarios
mal conçus : les fichiers JSON des scénarios se corrigent sans toucher au code.

**Où sont mes rapports ?** Dans `output_dir` / `--out-dir` (par défaut `reports/` ou le dossier courant) : `audit.html` et `audit.json`. Le dossier `reports/` est ignoré par git.

**Mes clés se retrouvent-elles dans le rapport ?** Non : seuls les noms des variables sont consignés, jamais leur valeur. Ne mettez pas de secret dans `--extra-body`, `--header` ni dans le gabarit
d'un chatbot classique : ces champs sont enregistrés.

**Puis-je utiliser HealthyGuard en intégration continue ?** Oui : `hg.gate(...)` dans un test, ou le code de sortie de `healthy-guard run` (0, 1, 2, 64).
