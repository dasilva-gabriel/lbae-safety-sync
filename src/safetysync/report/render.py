"""Rendu HTML du rapport (styles en ligne pour les clients mail, Outlook compris)."""

from __future__ import annotations

from datetime import date
from html import escape

from safetysync.report.analysis import (
    BLEU,
    GRIS,
    JAUNE,
    MAINTENANCE,
    OK,
    PANNE,
    ROUGE,
    VERT,
    DayCell,
)
from safetysync.report.collect import WeeklyReport

COLORS = {VERT: "#c6efce", JAUNE: "#ffeb9c", ROUGE: "#ffc7ce", BLEU: "#bdd7ee", GRIS: "#d9d9d9"}
HEAD = "#4caf50"
JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

TABLE = (
    'style="border-collapse:collapse;font-family:Arial,sans-serif;font-size:12px;'
    'margin-bottom:18px"'
)
TH = f'style="background:{HEAD};color:#fff;border:1px solid #fff;padding:6px;text-align:center"'
TD = 'style="border:1px solid #fff;padding:5px;text-align:center;background:{bg}"'


def subject(report: WeeklyReport) -> str:
    return f"Rapport maintenance du réseau du {report.monday:%d/%m/%Y} au {report.sunday:%d/%m/%Y}"


def render(report: WeeklyReport) -> str:
    parts = [
        '<div style="font-family:Arial,sans-serif;font-size:13px;color:#222">',
        f"<h2>{escape(subject(report))}</h2>",
        "<p>Bonjour à tous,</p>",
        "<p>Ce message automatique résume les inspections réalisées par chaque parc, l'état "
        "des actions de maintenance et l'état des jeux électroniques, organisés par région.</p>",
        _legend(report.opening_ratio),
        _summary(report),
        _priorities(report),
    ]
    for region in report.referentiel.regions:
        if not region.parcs:
            continue
        title = region.nom + (f" — {region.responsable}" if region.responsable else "")
        parts.append(f'<h3 style="color:{HEAD}">Région : {escape(title)}</h3>')
        parts.append(_inspections_table(report, region.parcs))
        parts.append(_actions_table(report, region.parcs))
        parts.append(_games_table(report, region.parcs))
    if report.warnings:
        parts.append("<h3>Points d'attention techniques</h3><ul>")
        parts += [f"<li>{escape(w)}</li>" for w in report.warnings]
        parts.append("</ul>")
    parts.append("<p>Excellente semaine à tous !</p></div>")
    return "\n".join(parts)


def _legend(ratio: float) -> str:
    items = [
        (VERT, "Inspection complète et conforme."),
        (
            JAUNE,
            "Inspection incomplète ou suspicion de fraude (durée trop courte, scores "
            "incohérents, saisie a posteriori).",
        ),
        (ROUGE, "Aucune inspection enregistrée un jour d'ouverture."),
        (BLEU, "Inspection effectuée, mais anticipée (commencée avant le jour contrôlé)."),
        (
            GRIS,
            f"Parc considéré fermé (moins de {ratio:.0%} des parcs ont réalisé une "
            "inspection ce jour-là).",
        ),
    ]
    lines = "".join(
        f'<li><span style="background:{COLORS[c]};padding:0 4px">{c.capitalize()}</span> : '
        f"{escape(t)}</li>"
        for c, t in items
    )
    return f"<p>Légende des couleurs :</p><ul>{lines}</ul>"


def _summary(report: WeeklyReport) -> str:
    parcs = report.referentiel.parcs
    expected = done = 0
    for parc in parcs:
        for day, cell in zip(report.days, report.day_cells[parc], strict=True):
            if day in report.open_days:
                expected += 1
                done += cell.color not in (ROUGE, GRIS)
    hebdos = sum(1 for p in parcs if report.hebdo[p].color == VERT)
    pannes = sum(1 for p in parcs for g in report.games.get(p, {}).values() if g.state == PANNE)
    maint = sum(
        1 for p in parcs for g in report.games.get(p, {}).values() if g.state == MAINTENANCE
    )
    stats = [report.stats[p] for p in parcs]
    rows = [
        ("Journaliers réalisés (jours ouverts)", f"{done} / {expected}"),
        ("Hebdos réalisés", f"{hebdos} / {len(parcs)}"),
        ("Jeux électroniques en panne", str(pannes)),
        ("Jeux électroniques à maintenir", str(maint)),
        ("Actions créées dans Mitti cette semaine", str(sum(s.creees for s in stats))),
        ("… transférées dans Smartsheet", str(sum(s.transferees for s in stats))),
        ("… rejetées (pas de Premier Signalement)", str(sum(s.rejetees for s in stats))),
        ("Actions à traiter dans Smartsheet", str(sum(s.a_traiter for s in stats))),
        ("Actions traitées cette semaine", str(sum(s.traitees_semaine for s in stats))),
    ]
    body = "".join(
        f'<tr><td {TD.format(bg="#f2f2f2")} align="left">{escape(k)}</td>'
        f"<td {TD.format(bg='#f2f2f2')}><b>{escape(v)}</b></td></tr>"
        for k, v in rows
    )
    return f"<h3>Synthèse du réseau</h3><table {TABLE}>{body}</table>"


def _priorities(report: WeeklyReport) -> str:
    top = [p for p in report.priorities if p.score > 0][: report.priority_top]
    if not top:
        return "<h3>Parcs prioritaires</h3><p>Aucun parc ne demande d'attention particulière.</p>"
    head = "".join(f"<th {TH}>{h}</th>" for h in ("#", "Parc", "Région", "Score", "Pourquoi"))
    rows = []
    for rank, p in enumerate(top, start=1):
        region = report.referentiel.region_of(p.site)
        bg = COLORS[ROUGE] if rank <= 3 else COLORS[JAUNE]
        rows.append(
            f"<tr><td {TD.format(bg=bg)}>{rank}</td><td {TD.format(bg=bg)} nowrap><b>"
            f"{escape(p.site)}</b></td><td {TD.format(bg=bg)}>"
            f"{escape(region.nom if region else '')}</td><td {TD.format(bg=bg)}>{p.score:g}</td>"
            f'<td {TD.format(bg=bg)} align="left">{escape(" ; ".join(p.reasons))}</td></tr>'
        )
    return f"<h3>Parcs prioritaires</h3><table {TABLE}><tr>{head}</tr>{''.join(rows)}</table>"


def _cell(cell: DayCell) -> str:
    text = escape(cell.text)
    if cell.link:
        text = f'<a href="{escape(cell.link)}" style="color:#000">{text}</a>'
    if cell.issues:
        text += "<br><small>⚠ " + escape(" ; ".join(cell.issues)) + "</small>"
    return f"<td {TD.format(bg=COLORS[cell.color])}>{text}</td>"


def _inspections_table(report: WeeklyReport, parcs: list[str]) -> str:
    head = f"<th {TH}>Parc</th><th {TH}>Hebdo</th>" + "".join(
        f"<th {TH}>{JOURS[d.weekday()]} {d:%d/%m}</th>" for d in report.days
    )
    rows = []
    for parc in parcs:
        cells = "".join(_cell(c) for c in report.day_cells[parc])
        rows.append(
            f"<tr><td {TD.format(bg='#f2f2f2')} nowrap><b>{escape(parc)}</b></td>"
            f"{_cell(report.hebdo[parc])}{cells}</tr>"
        )
    return f"<p><b>Inspections</b></p><table {TABLE}><tr>{head}</tr>{''.join(rows)}</table>"


def _actions_table(report: WeeklyReport, parcs: list[str]) -> str:
    headers = (
        "Parc",
        "Créées",
        "Transférées",
        "Rejetées",
        "Non transférées",
        "À traiter",
        "Dont anciennes",
        "Plus ancienne",
        "Traitées",
    )
    head = "".join(f"<th {TH}>{h}</th>" for h in headers)
    rows = []
    for parc in parcs:
        s = report.stats[parc]
        oldest = f"{s.plus_ancienne:%d/%m/%Y}" if s.plus_ancienne else "-"
        bg_todo = COLORS[ROUGE] if s.anciennes else COLORS[VERT] if not s.a_traiter else "#f2f2f2"
        values = [s.creees, s.transferees, s.rejetees, s.ouvertes]
        cells = "".join(f"<td {TD.format(bg='#f2f2f2')}>{v}</td>" for v in values)
        rows.append(
            f"<tr><td {TD.format(bg='#f2f2f2')} nowrap><b>{escape(parc)}</b></td>{cells}"
            f"<td {TD.format(bg=bg_todo)}>{s.a_traiter}</td>"
            f"<td {TD.format(bg=bg_todo)}>{s.anciennes}</td>"
            f"<td {TD.format(bg='#f2f2f2')}>{oldest}</td>"
            f"<td {TD.format(bg='#f2f2f2')}>{s.traitees_semaine}</td></tr>"
        )
    note = (
        "<small>Créées / Transférées / Rejetées : actions Mitti de la semaine. "
        "Non transférées : actions encore « To do » dans Mitti. À traiter : lignes AC non "
        "cochées (Statut Mgr) dans le Smartsheet Travaux. Traitées : lignes cochées avec "
        "une date de fin dans la semaine.</small>"
    )
    return (
        f"<p><b>Actions de maintenance</b></p><table {TABLE}><tr>{head}</tr>"
        f"{''.join(rows)}</table>{note}"
    )


def _games_table(report: WeeklyReport, parcs: list[str]) -> str:
    names = report.game_names
    if not names:
        return ""
    head = f"<th {TH}>Parc</th>" + "".join(f"<th {TH}>{escape(n)}</th>" for n in names)
    rows = []
    for parc in parcs:
        states = report.games.get(parc)
        if not states:
            rows.append(
                f"<tr><td {TD.format(bg='#f2f2f2')} nowrap><b>{escape(parc)}</b></td>"
                f'<td {TD.format(bg=COLORS[GRIS])} colspan="{len(names)}">'
                "Aucun hebdo terminé récemment</td></tr>"
            )
            continue
        cells = []
        for name in names:
            g = states.get(name)
            if g is None:
                cells.append(f"<td {TD.format(bg='#f2f2f2')}>-</td>")
            elif g.state == PANNE:
                since = f"<br><small>{g.weeks_down} sem.</small>" if g.weeks_down > 1 else ""
                cells.append(f"<td {TD.format(bg=COLORS[ROUGE])}>En panne{since}</td>")
            elif g.state == MAINTENANCE:
                cells.append(f"<td {TD.format(bg=COLORS[JAUNE])}>Maintenance</td>")
            elif g.state == OK:
                cells.append(f"<td {TD.format(bg=COLORS[VERT])}>OK</td>")
            else:
                cells.append(f"<td {TD.format(bg='#f2f2f2')}>?</td>")
        rows.append(
            f"<tr><td {TD.format(bg='#f2f2f2')} nowrap><b>{escape(parc)}</b></td>"
            f"{''.join(cells)}</tr>"
        )
    return (
        f"<p><b>Jeux électroniques (dernier hebdo)</b></p><table {TABLE}><tr>{head}</tr>"
        f"{''.join(rows)}</table>"
    )


def day_label(day: date) -> str:
    return f"{JOURS[day.weekday()]} {day:%d/%m}"
