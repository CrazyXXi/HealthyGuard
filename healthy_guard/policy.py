from healthy_guard.models import CheckResult, Decision, Severity, Status


def decide_with_reasons(
    results: list[CheckResult], max_major_fails: int = 3
) -> tuple[Decision, list[str]]:
    """Décision globale de gouvernance, avec les raisons qui la justifient.

    Une décision de blocage doit pouvoir être expliquée et contestée : on renvoie donc
    aussi la liste des motifs, avec les identifiants des cas concernés.
    """
    fails = [r for r in results if r.status == Status.FAIL]
    # Une erreur technique (chatbot injoignable, quota...) n'est pas un cas qu'un humain peut trancher :
    # il n'y a rien à lire. Il faut relancer. On l'annonce séparément, mais on ne peut pas certifier.
    errors = [r for r in results if r.tags.get("error")]
    reviews = [r for r in results if r.status == Status.HUMAN_REVIEW and not r.tags.get("error")]
    critical = [r for r in fails if r.severity == Severity.CRITICAL]
    major = [r for r in fails if r.severity == Severity.MAJOR]

    def ids(items: list[CheckResult]) -> str:
        unique = list(dict.fromkeys(r.check_id for r in items))
        shown = ", ".join(unique[:8])
        return shown + (f" … (+{len(unique) - 8})" if len(unique) > 8 else "")

    # Un audit incomplet doit toujours le dire, même quand le blocage est déjà acquis.
    incomplete = ([f"{len(errors)} cas sans verdict (erreur technique : relancer avec --retry-errors, "
                   f"ex. avec --min-interval) : {ids(errors)}"] if errors else [])

    blocking: list[str] = []
    # Règle 1 : un échec critique, dans n'importe quel pilier, bloque
    if critical:
        blocking.append(f"{len(critical)} échec(s) critique(s) : {ids(critical)}")
    # Règle 2 : trop d'échecs majeurs bloquent
    if len(major) >= max_major_fails:
        blocking.append(
            f"{len(major)} échec(s) majeur(s) (seuil de blocage : {max_major_fails}) : {ids(major)}"
        )
    if blocking:
        return Decision.BLOCKED, blocking + incomplete

    # Règle 3 : un échec restant ou une revue humaine demandée
    pending: list[str] = []
    if fails:
        pending.append(f"{len(fails)} échec(s) à examiner : {ids(fails)}")
    if reviews:
        pending.append(f"{len(reviews)} cas à relire par un humain : {ids(reviews)}")
    pending += incomplete
    if pending:
        return Decision.REVIEW_REQUIRED, pending

    # Règle 4 : les warnings seuls ne bloquent pas
    return Decision.PASS, ["Aucun échec ni revue humaine en attente."]

