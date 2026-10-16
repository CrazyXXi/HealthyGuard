"""Rapport HTML autonome (aucune ressource externe) pour relecture par des non-techniciens.

Tout texte venant du chatbot ou des datasets est échappé : une réponse de chatbot est une
donnée non fiable et ne doit jamais pouvoir injecter du HTML dans le rapport.
"""
from __future__ import annotations

from html import escape
from pathlib import Path

from healthy_guard.models import AuditReport, Status
from healthy_guard.reporting.json_report import report_to_dict

MAX_DETAILS = 150
MAX_TEXT = 700

DECISION_LABELS = {
    "pass": ("Déploiement autorisé", "ok"),
    "review_required": ("Revue humaine requise", "warn"),
    "blocked": ("Déploiement bloqué", "bad"),
}
STATUS_LABELS = {"pass": "réussite", "fail": "échec", "warning": "avertissement",
                 "human_review": "revue humaine"}
PILLAR_LABELS = {
    "clinical_safety": "Sécurité clinique", "transparency": "Transparence", "equity": "Équité",
    "privacy": "Confidentialité", "accountability": "Responsabilité & traçabilité",
    "consent": "Information & consentement",
}

CSS = """
:root{--bg:#fff;--fg:#1c2330;--muted:#5d6778;--line:#dde2ea;--card:#f6f8fb;
--ok:#1a7f4b;--warn:#a15c00;--bad:#b3261e;--okbg:#e5f4ec;--warnbg:#fdf0dc;--badbg:#fbe7e5}
@media (prefers-color-scheme:dark){:root{--bg:#10151d;--fg:#e6eaf0;--muted:#97a1b1;--line:#2a3340;
--card:#171e29;--ok:#5fcf93;--warn:#f0b35a;--bad:#ff8a80;--okbg:#14301f;--warnbg:#33260f;--badbg:#3a1815}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1000px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.5rem;margin:0 0 4px}h2{font-size:1.15rem;margin:32px 0 10px}
.muted{color:var(--muted)}.banner{border-radius:10px;padding:16px 18px;margin:16px 0}
.banner.ok{background:var(--okbg);color:var(--ok)}.banner.warn{background:var(--warnbg);color:var(--warn)}
.banner.bad{background:var(--badbg);color:var(--bad)}.banner strong{font-size:1.2rem}
.banner ul{margin:8px 0 0;padding-left:20px;color:var(--fg)}
table{border-collapse:collapse;width:100%;margin:8px 0}th,td{border-bottom:1px solid var(--line);
padding:6px 8px;text-align:left;vertical-align:top}th{font-weight:600;color:var(--muted)}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.scroll{overflow-x:auto}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:10px 0}
.tag{display:inline-block;border-radius:999px;padding:1px 9px;font-size:.8rem;margin-right:6px;
border:1px solid var(--line)}.tag.fail{color:var(--bad)}.tag.human_review,.tag.warning{color:var(--warn)}
.tag.pass{color:var(--ok)}pre{white-space:pre-wrap;word-break:break-word;margin:4px 0 8px;
font:13px/1.45 ui-monospace,Consolas,monospace}.k{font-weight:600}
code{font:13px ui-monospace,Consolas,monospace;word-break:break-all}
"""


def _pct(rate: dict) -> str:
    if rate["rate"] is None:
        return "—"
    return f"{rate['rate'] * 100:.1f} % <span class='muted'>[{rate['ci_low'] * 100:.0f}–{rate['ci_high'] * 100:.0f}]</span>"


def _cut(text: str) -> str:
    return escape(text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + " […]")


def _reading_section(reading: dict | None) -> str:
    if not reading:
        return ""
    cards = []
    for r in reading["pillars"].values():
        weak = ""
        if r["weak_points"]:
            weak = "<p><b>Points faibles :</b> " + ", ".join(
                f"{escape(str(w['category']))} ({w['failures']}" + (f"/{w['cases']}" if w["cases"] else "") + " échec(s))"
                for w in r["weak_points"]) + "</p>"
        cards.append(f"<div class='card'><h3>{escape(r['label'])} — {escape(r['level'])} "
                     f"<span class='muted'>(fiabilité du chiffre : {escape(r['reliability'])})</span></h3>"
                     f"<p class='muted'>{escape(r['question'])}</p>"
                     + "".join(f"<p>{escape(d)}</p>" for d in r["details"]) + weak + "</div>")
    guide = "".join(f"<li>{escape(t)}</li>" for t in reading["how_to_read"])
    return (f"<h2>Lecture des résultats</h2><p><b>{escape(' '.join(reading['summary']))}</b></p>{''.join(cards)}"
            f"<details><summary>Comment lire ces chiffres ?</summary><ul>{guide}</ul></details>")


def _pillar_table(stats: dict) -> str:
    rows = []
    for pillar, s in stats["by_pillar"].items():
        c = s["by_status"]
        rows.append(
            f"<tr><td>{escape(PILLAR_LABELS.get(pillar, pillar))}</td><td class='num'>{s['n']}</td>"
            f"<td class='num'>{c['pass']}</td><td class='num'>{c['fail']}</td>"
            f"<td class='num'>{c['warning']}</td><td class='num'>{c['human_review']}</td>"
            f"<td class='num'>{_pct(s['pass_rate'])}</td></tr>")
    return ("<div class='scroll'><table><tr><th>Pilier</th><th class='num'>Contrôles</th>"
            "<th class='num'>Réussite</th><th class='num'>Échec</th><th class='num'>Avert.</th>"
            "<th class='num'>Revue</th><th class='num'>Taux de réussite [IC 95 %]</th></tr>"
            + "".join(rows) + "</table></div>")


def _breakdown_table(title: str, groups: dict, label=lambda k: k) -> str:
    rows = "".join(
        f"<tr><td>{escape(label(k))}</td><td class='num'>{s['n']}</td>"
        f"<td class='num'>{_pct(s['pass_rate'])}</td><td class='num'>{_pct(s['fail_rate'])}</td></tr>"
        for k, s in groups.items())
    return (f"<h2>{escape(title)}</h2><div class='scroll'><table><tr><th>Groupe</th>"
            "<th class='num'>n</th><th class='num'>Réussite [IC 95 %]</th>"
            f"<th class='num'>Échec [IC 95 %]</th></tr>{rows}</table></div>")


def _equity_section(equity: dict) -> str:
    if not equity:
        return ""
    if not equity["comparisons"]:
        return ("<h2>Équité entre profils</h2><p class='muted'>Pas assez de données pour comparer "
                "des profils (échantillon trop réduit).</p>")
    base = equity["baseline"]
    rows = []
    for c in equity["comparisons"]:
        gap = "—" if c["gap_vs_baseline"] is None else f"{c['gap_vs_baseline'] * 100:+.1f} pts"
        p = "—" if c["p_value"] is None else f"{c['p_value']:.4f}"
        flag = " <span class='tag fail'>écart significatif</span>" if c["significant"] else ""
        rows.append(
            f"<tr><td>{escape(c['attribute'])} = {escape(c['value'])}{flag}</td>"
            f"<td class='num'>{_pct(c['pass_rate'])}</td><td class='num'>{gap}</td>"
            f"<td class='num'>{p}</td><td class='num'>{c['mean_response_length']:.0f}</td></tr>")
    significant = equity["significant_disparities"]
    verdict = ("Écarts statistiquement significatifs : <strong>" + escape(", ".join(significant))
               + "</strong>.") if significant else "Aucun écart statistiquement significatif détecté."
    power = (
        f"<div class='banner warn'><strong>Faible puissance statistique.</strong> Seulement "
        f"{equity['min_cases_per_profile']} cas par profil : « aucun écart significatif » ne prouve "
        "pas l'absence de biais. Regardez le nombre d'échecs par profil ci-dessous et relisez-les.</div>"
    ) if equity.get("low_power") else ""
    failures = "".join(
        f"<li>{escape(k)} : {n} échec(s)</li>"
        for k, n in equity.get("failures_by_profile", {}).items() if n)
    failures = f"<p class='k'>Profils avec échecs :</p><ul>{failures}</ul>" if failures else ""
    return (
        "<h2>Équité entre profils</h2>" + power +
        f"<p>{verdict} <span class='muted'>Test exact de Fisher contre le profil neutre ; seuil "
        f"corrigé de Bonferroni : p &lt; {equity['bonferroni_threshold']}.</span></p>"
        "<div class='scroll'><table><tr><th>Profil</th><th class='num'>Réussite [IC 95 %]</th>"
        "<th class='num'>Écart vs neutre</th><th class='num'>p</th>"
        "<th class='num'>Longueur moyenne</th></tr>"
        f"<tr><td>profil neutre (référence)</td><td class='num'>{_pct(base['pass_rate'])}</td>"
        f"<td class='num'>—</td><td class='num'>—</td>"
        f"<td class='num'>{base['mean_response_length'] or '—'}</td></tr>"
        + "".join(rows) + "</table></div>" + failures)


def _repeatability_section(rep: dict) -> str:
    if rep.get("repeats", 1) < 2:
        return f"<h2>Répétabilité</h2><p class='muted'>{escape(rep.get('note', ''))}</p>"
    return ("<h2>Répétabilité</h2>"
            f"<p>{rep['cases']} cas rejoués {rep['repeats']} fois : "
            f"<strong>{rep['unstable_cases']}</strong> changent de verdict "
            f"({_pct(rep['unstable_rate'])}).</p>")


PRIORITY_LABELS = {"critical": "critique", "major": "majeur", "minor": "mineur"}
KIND_LABELS = {"prompt": "prompt système", "prompt+infra": "prompt système + plateforme", "config": "configuration de l'API",
               "infra": "infrastructure", "process": "processus"}
LEVEL_LABELS = {"ok": "ok", "warning": "attention", "error": "échec"}


def _recommendations_section(recommendations: list[dict] | None) -> str:
    if recommendations is None:
        return ""
    if not recommendations:
        return "<h2>Recommandations</h2><p>Aucune : aucun échec ni point de configuration à corriger.</p>"
    cards = []
    for number, rec in enumerate(recommendations, 1):
        evidence = "".join(f"<li>{escape(e[:220])}</li>" for e in rec.get("evidence", [])[:3])
        snippet = (f"<div class='muted'>Extrait de prompt système à relire et adapter :</div><pre>{escape(rec['prompt_snippet'])}</pre>"
                   if rec.get("prompt_snippet") else "")
        cases = (f"<div class='muted'>Cas concernés : {escape(', '.join(rec['cases'][:12]))}"
                 f"{' …' if len(rec['cases']) > 12 else ''}</div>" if rec.get("cases") else "")
        cards.append(
            "<div class='card'>"
            f"<span class='tag fail'>{PRIORITY_LABELS.get(rec['priority'], rec['priority'])}</span>"
            f"<span class='tag'>{escape(KIND_LABELS.get(rec['kind'], rec['kind']))}</span>"
            f"<strong>{number}. {escape(rec['title'])}</strong>"
            f"<div><span class='k'>Observé :</span> {escape(rec['observed'])}</div>"
            + (f"<ul>{evidence}</ul>" if evidence else "")
            + f"<div><span class='k'>À faire :</span> {escape(rec['action'])}</div>{snippet}{cases}</div>")
    return ("<h2>Recommandations</h2><p class='muted'>Suggestions à relire (idéalement par un soignant), à appliquer, puis à vérifier "
            "par un nouvel audit comparé à celui-ci (<code>compare</code>).</p>" + "".join(cards))


def _prompt_section(review: dict | None) -> str:
    if review is None:
        return ""
    head = (f"<p class='muted'>Empreinte {escape(review['sha256'])} · {review['chars']} caractères · "
            f"{len(review['present'])} garde-fou(s) présent(s). Le texte du prompt n'est pas enregistré dans ce rapport.</p>")
    if not review["findings"]:
        return f"<h2>Prompt système</h2>{head}<p>Aucun constat de la revue automatique.</p>"
    rows = "".join(
        f"<tr><td><span class='tag {'fail' if f['priority'] == 'critical' else ''}'>{PRIORITY_LABELS.get(f['priority'], f['priority'])}</span></td>"
        f"<td>{escape(f['title'])}</td><td>{escape(f['detail'])}</td></tr>" for f in review["findings"])
    return (f"<h2>Prompt système</h2>{head}<div class='scroll'><table><tr><th>Gravité</th><th>Constat</th><th>Détail</th></tr>{rows}</table></div>")


def _scope_section(scope: dict | None, card: dict | None) -> str:
    """Ce que l'audit a couvert, ce qu'il n'a PAS couvert : sans cela, un « autorisé » laisse croire à une garantie qui n'existe pas."""
    if not scope:
        return ""
    head = ""
    if card:
        bits = [f"<strong>{escape(card.get('name', ''))}</strong>"]
        for label, key in (("usage prévu", "intended_use"), ("public", "audience")):
            if card.get(key):
                bits.append(f"{label} : {escape(card[key])}")
        if card.get("languages"):
            bits.append("langues déclarées : " + escape(", ".join(card["languages"])))
        head = f"<p>{' · '.join(bits)}</p>"
    lists = "".join(
        f"<div><span class='k'>{title}</span><ul>{''.join(f'<li>{escape(x)}</li>' for x in scope.get(key, []))}</ul></div>"
        for title, key in (("Couvert par cet audit", "covered"), ("NON couvert (à examiner par ailleurs)", "not_covered"),
                           ("Limites de ses conclusions", "limits")) if scope.get(key))
    return f"<h2>Périmètre de l'audit</h2>{head}<div class='card'>{lists}</div>"


def _connection_section(findings: list[dict] | None) -> str:
    if not findings:
        return ""
    rows = "".join(f"<tr><td>{LEVEL_LABELS.get(f['level'], f['level'])}</td><td>{escape(f['title'])}"
                   f"{'<div class=muted>' + escape(f['detail']) + '</div>' if f.get('detail') else ''}</td></tr>" for f in findings)
    return f"<h2>Connexion au chatbot</h2><div class='scroll'><table>{rows}</table></div>"


def _judges_section(judges: dict, metadata: dict) -> str:
    parts = []
    for warning in metadata.get("warnings", []):
        parts.append(f"<div class='banner warn'><strong>Attention.</strong> {escape(warning)}</div>")
    if judges:
        rows = "".join(
            f"<tr><td>{escape(name)}</td><td class='num'>{_pct(r)}</td></tr>"
            for name, r in judges["fail_rate_by_judge"].items())
        parts.append(
            f"<p>Les juges divergent sur {_pct(judges['disagreement'])} des cas "
            f"({judges['judged']} évalués par un jury) ; ces cas sont renvoyés à un humain selon la politique.</p>"
            "<div class='scroll'><table><tr><th>Juge</th><th class='num'>Taux d'échecs prononcés</th></tr>"
            f"{rows}</table></div>")
    reviews = metadata.get("human_reviews")
    if reviews:
        parts.append(
            f"<p><strong>Revue humaine :</strong> {reviews['applied']} décision(s) par "
            f"{escape(', '.join(reviews['reviewers']))} ; {reviews['still_pending']} cas encore en attente. "
            f"Décision avant revue : {escape(str(metadata.get('decision_before_review')))}.</p>")
    return ("<h2>Juges et revue humaine</h2>" + "".join(parts)) if parts else ""


def _details(results: list[dict]) -> str:
    shown = [r for r in results if r["status"] in ("fail", "human_review")]
    order = {"critical": 0, "major": 1, "minor": 2}
    shown.sort(key=lambda r: (r["status"] != "fail", order[r["severity"]], r["check_id"]))
    parts = []
    for r in shown[:MAX_DETAILS]:
        speakers = {"user": "Utilisateur", "assistant": "Chatbot"}
        conversation = "\n".join(
            m["content"] if m["role"] == "session" else f"{speakers.get(m['role'], m['role'])} : {m['content']}"
            for m in r["conversation"]) or r["prompt"]
        evidence = (f"<div><span class='k'>Preuves :</span> {escape(' | '.join(r['evidence']))}</div>"
                    if r["evidence"] else "")
        parts.append(
            "<div class='card'>"
            f"<span class='tag {r['status']}'>{STATUS_LABELS[r['status']]}</span>"
            f"<span class='tag'>{escape(r['severity'])}</span>"
            f"<span class='tag'>{escape(PILLAR_LABELS.get(r['pillar'], r['pillar']))}</span>"
            f"<code>{escape(r['check_id'])}</code>"
            f"<div><span class='k'>Pourquoi :</span> {escape(r['explanation'])}</div>"
            f"<div><span class='k'>Attendu :</span> {escape(r['expected'])}</div>{evidence}"
            f"<pre>{_cut(conversation)}</pre>"
            f"<div class='muted'>Réponse finale :</div><pre>{_cut(r['response'] or '(aucune)')}</pre>"
            "</div>")
    hidden = len(shown) - MAX_DETAILS
    note = f"<p class='muted'>… et {hidden} autre(s), voir le rapport JSON.</p>" if hidden > 0 else ""
    return "".join(parts) + note if parts else "<p>Aucun échec ni cas à relire.</p>"


def _provenance(data: dict) -> str:
    m = data["metadata"]
    datasets = "".join(
        f"<tr><td>{escape(n)}</td><td class='num'>{d.get('cases_run', d['cases'])}/{d['cases']}</td>"
        f"<td><code>{escape(d['sha256'][:16])}…</code></td></tr>"
        for n, d in m.get("datasets", {}).items())
    chatbot = escape(", ".join(f"{k}={v}" for k, v in m.get("chatbot", {}).items()))
    judge = escape(str(m.get("judge", {})))
    settings = escape(", ".join(f"{k}={v}" for k, v in m.get("settings", {}).items()))
    return (
        "<h2>Traçabilité</h2><div class='scroll'><table>"
        f"<tr><th>Version de HealthyGuard</th><td>{escape(str(m.get('healthy_guard_version')))}</td></tr>"
        f"<tr><th>Début → fin</th><td>{escape(str(m.get('started_at')))} → {escape(str(m.get('finished_at')))}"
        f" ({escape(str(m.get('duration_s')))} s)</td></tr>"
        f"<tr><th>Chatbot audité</th><td>{chatbot}</td></tr><tr><th>Juge</th><td>{judge}</td></tr>"
        f"<tr><th>Paramètres</th><td>{settings}</td></tr>"
        f"<tr><th>Intégrité des résultats</th><td><code>{escape(data['integrity']['results_sha256'])}</code>"
        "</td></tr></table></div>"
        "<div class='scroll'><table><tr><th>Dataset</th><th class='num'>Cas exécutés</th>"
        f"<th>Empreinte sha256</th></tr>{datasets}</table></div>")


def render_html(report: AuditReport, data: dict | None = None) -> str:
    data = data or report_to_dict(report)
    stats = data["stats"]
    label, level = DECISION_LABELS.get(data["decision"], ("Décision inconnue", "warn"))
    reasons = "".join(f"<li>{escape(r)}</li>" for r in data["metadata"].get("decision_reasons", []))
    failures = sum(1 for r in data["results"] if r["status"] == Status.FAIL.value)
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HealthyGuard — rapport d'audit</title><style>{CSS}</style></head><body><main>
<h1>HealthyGuard — rapport d'audit</h1>
<div class="muted">{stats['totals']['n']} contrôles · {stats['totals']['unique_cases']} cas distincts · {failures} échec(s)</div>
<div class="banner {level}"><strong>{label}</strong><ul>{reasons}</ul></div>
{_recommendations_section(data.get("recommendations"))}
{_prompt_section(data.get("prompt_review"))}
{_connection_section(data.get("connection"))}
{_scope_section(data.get("scope") or data["metadata"].get("scope"), data["metadata"].get("chatbot_card"))}
{_reading_section(stats.get("interpretation"))}
<h2>Résultats par pilier</h2>{_pillar_table(stats)}
{_breakdown_table("Par sévérité", stats["totals"]["by_severity"])}
{_breakdown_table("Par style de formulation", stats["by_style"])}
{_breakdown_table("Par catégorie", stats["by_category"])}
{_equity_section(stats["equity"])}
{_repeatability_section(stats["repeatability"])}
{_judges_section(stats.get("judges", {}), data["metadata"])}
<h2>Échecs et cas à relire</h2>{_details(data["results"])}
{_provenance(data)}
<p class="muted">Les verdicts automatiques par règles ou par modèle ne remplacent pas la relecture
d'un professionnel de santé. Les cas « revue humaine » exigent une décision humaine.</p>
</main></body></html>"""


def write_html(report: AuditReport, path: str | Path, data: dict | None = None) -> None:
    Path(path).write_text(render_html(report, data), encoding="utf-8")
