"""Rendu HTML du rapport, pensé pour Outlook « classique » (moteur Word).

Contraintes respectées : mise en page uniquement en tableaux, largeur fixe 760 px,
couleurs en attribut `bgcolor` + style en ligne, pas de CSS externe, de flex ni de
marges sur des div, polices système. Le logo est une image intégrée (cid:logo).

Ordre des blocs : bandeau d'en-tête → État du réseau (indicateurs vs S-1) → Top 5 des
parcs prioritaires → Inspections de la semaine (tableau compact) → Focus actions →
Focus jeux électroniques → notes techniques.
"""

from __future__ import annotations

import re
from html import escape
from pathlib import Path

from safetysync.report.analysis import (
    BLEU,
    GRIS,
    JAUNE,
    MAINTENANCE,
    OK,
    ORANGE,
    PANNE,
    ROUGE,
    STATE_LABELS,
    VERT,
    DayCell,
    Kpi,
    fmt_duration,
    rate,
    sum_counts,
)
from safetysync.report.collect import WeeklyReport

LOGO_PATH = Path(__file__).with_name("logo.png")

WIDTH = 760
FONT = "font-family:Arial,Helvetica,sans-serif;"
BRAND = "#E8114A"  # rouge LBAE
INK = "#222222"
MUTED = "#777777"
LINE = "#E3E3E3"
SOFT = "#F6F6F6"

# fond, texte
CELL = {
    VERT: ("#C9EBCB", "#1E6B2A"),
    JAUNE: ("#FFE08A", "#7A5200"),
    ORANGE: ("#FFC48C", "#8A3B00"),
    ROUGE: ("#F6B3B3", "#9B1111"),
    BLEU: ("#C7DDF7", "#1F4E8C"),
    GRIS: ("#EFEFEF", "#A0A0A0"),
}
TONE = {"good": "#1E8E3E", "bad": "#C62828", "neutral": "#888888"}
ARROW = {"up": "▲", "down": "▼", "flat": "=", "none": ""}
JOURS = ["L", "M", "M", "J", "V", "S", "D"]
JOURS_LONG = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
TABLE = 'role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"'


def subject(report: WeeklyReport) -> str:
    return f"Rapport maintenance du réseau du {report.monday:%d/%m/%Y} au {report.sunday:%d/%m/%Y}"


def logo_bytes() -> bytes:
    return LOGO_PATH.read_bytes()


def render(report: WeeklyReport, logo_src: str = "cid:logo") -> str:
    blocks = [
        _header(report, logo_src),
        _section("État du réseau", "Semaine écoulée comparée à la précédente (S-1)."),
        _kpis(report.kpis),
        _section(
            f"Top {report.priority_top} des parcs prioritaires",
            "Priorité de maintenance (sur 100) : note des contrôles et actions (nouvelles, "
            "stock, ancienneté).",
        ),
        _top(report),
        _section(
            "Inspections de la semaine",
            "Un journalier par jour, puis l'hebdo (H). Cliquez une case pour ouvrir le rapport.",
        ),
        _inspections(report),
        _legend(report),
        _section("Focus actions", "Actions Mitti créées dans la semaine et stock Smartsheet."),
        _actions(report),
        _section(
            "Focus jeux électroniques",
            "État au dernier hebdo et taux de panne par parc et par machine "
            f"({report.quarters[1]} en cours, {report.quarters[0]} entre parenthèses).",
        ),
        _machines(report),
        _notes(report),
        _footer(),
    ]
    body = "\n".join(b for b in blocks if b)
    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(subject(report))}</title></head>
<body style="margin:0;padding:0;background:#EDEDED">
<table {TABLE} bgcolor="#EDEDED"><tr><td align="center" style="padding:16px 8px">
<table role="presentation" width="{WIDTH}" cellpadding="0" cellspacing="0" border="0"
 bgcolor="#FFFFFF" style="width:{WIDTH}px;background:#FFFFFF">
{body}
</table></td></tr></table></body></html>"""


# --------------------------------------------------------------------- blocs
def _row(content: str, pad: str = "0 24px") -> str:
    return f'<tr><td style="padding:{pad};{FONT}color:{INK}">{content}</td></tr>'


def _text(content: str, size: int = 11, color: str = MUTED, top: int = 6) -> str:
    return (
        f'<table {TABLE}><tr><td style="padding-top:{top}px;{FONT}font-size:{size}px;'
        f'color:{color};line-height:{size + 4}px">{content}</td></tr></table>'
    )


def _header(report: WeeklyReport, logo_src: str) -> str:
    title = (
        f'<span style="{FONT}font-size:20px;font-weight:bold;color:{INK}">'
        "Suivi maintenance du réseau</span><br>"
        f'<span style="{FONT}font-size:14px;color:{MUTED}">Semaine du '
        f"{report.monday:%d/%m} au {report.sunday:%d/%m/%Y}</span>"
    )
    return (
        f'<tr><td bgcolor="{BRAND}" height="6" style="font-size:0;line-height:0">&nbsp;</td>'
        "</tr>"
        + _row(
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td width="46" valign="middle"><img src="{logo_src}" width="46" height="56" '
            'alt="LBAE" style="display:block;border:0"></td>'
            f'<td valign="middle" style="padding-left:14px">{title}</td></tr></table>',
            "18px 24px 4px",
        )
    )


def _section(title: str, hint: str = "") -> str:
    hint_html = (
        f'<br><span style="{FONT}font-size:12px;color:{MUTED};font-weight:normal">'
        f"{escape(hint)}</span>"
        if hint
        else ""
    )
    return _row(
        f'<table {TABLE}><tr><td style="border-left:4px solid {BRAND};padding:2px 0 2px 10px;'
        f'{FONT}font-size:16px;font-weight:bold;color:{INK}">{escape(title)}{hint_html}</td>'
        "</tr></table>",
        "24px 24px 10px",
    )


def _kpis(kpis: list[Kpi]) -> str:
    width = (WIDTH - 48) // max(len(kpis), 1)
    tiles = []
    for k in kpis:
        trend = f"{ARROW[k.trend]} {k.delta}" if k.trend != "none" else "&nbsp;"
        hint = escape(k.hint) if k.hint else "&nbsp;"
        tiles.append(
            f'<td width="{width}" valign="top" align="center" bgcolor="{SOFT}" '
            f'style="padding:10px 4px;border:2px solid #FFFFFF;{FONT}">'
            f"<table {TABLE}>"
            f'<tr><td align="center" height="28" valign="top" style="{FONT}font-size:11px;'
            f'color:{MUTED};line-height:13px">{escape(k.label)}</td></tr>'
            f'<tr><td align="center" style="{FONT}font-size:22px;font-weight:bold;color:{INK};'
            f'line-height:28px">{escape(k.value)}</td></tr>'
            f'<tr><td align="center" style="{FONT}font-size:11px;font-weight:bold;'
            f'color:{TONE[k.tone]};line-height:15px">{trend}</td></tr>'
            f'<tr><td align="center" style="{FONT}font-size:10px;color:{MUTED};'
            f'line-height:13px">{hint}</td></tr></table></td>'
        )
    return _row(f"<table {TABLE}><tr>{''.join(tiles)}</tr></table>")


def _top(report: WeeklyReport) -> str:
    top = [p for p in report.priorities if p.score > 0][: report.priority_top]
    if not top:
        return _row(
            _text("Aucun parc ne demande d'attention particulière cette semaine.", 13, INK, 0)
        )
    rows = []
    cell = f"border-bottom:1px solid {LINE};{FONT}"
    for rank, p in enumerate(top, start=1):
        region = report.referentiel.region_of(p.site)
        badge = BRAND if rank <= 3 else "#F28A2E"
        rows.append(
            f'<tr><td width="34" valign="top" style="padding:8px 0;{cell}">'
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td width="26" height="26" align="center" bgcolor="{badge}" style="{FONT}'
            f'font-size:13px;font-weight:bold;color:#FFFFFF">{rank}</td></tr></table></td>'
            f'<td width="150" valign="top" style="padding:8px;{cell}font-size:14px;'
            f'font-weight:bold;color:{INK}">{escape(p.site)}<br>'
            f'<span style="font-size:11px;font-weight:normal;color:{MUTED}">'
            f"{escape(region.nom if region else '')}</span></td>"
            f'<td valign="top" style="padding:8px;{cell}font-size:12px;color:{INK};'
            f'line-height:17px">{escape(" · ".join(p.reasons))}</td>'
            f'<td width="48" align="right" valign="top" style="padding:8px 0;{cell}'
            f'font-size:10px;color:{MUTED}">priorité<br>'
            f'<b style="font-size:15px;color:{INK}">{p.score:g}</b>'
            f'<span style="font-size:10px">/100</span></td></tr>'
        )
    return _row(f"<table {TABLE}>{''.join(rows)}</table>")


def _status_cell(cell: DayCell, width: int = 26) -> str:
    bg, fg = CELL[cell.color]
    title = escape(" ; ".join([cell.text, *cell.issues]))
    symbol = escape(cell.symbol)
    if cell.link:
        symbol = (
            f'<a href="{escape(cell.link)}" style="color:{fg};text-decoration:none">{symbol}</a>'
        )
    return (
        f'<td width="{width}" height="22" align="center" bgcolor="{bg}" title="{title}" '
        f'style="border:2px solid #FFFFFF;{FONT}font-size:12px;font-weight:bold;color:{fg}">'
        f"{symbol}</td>"
    )


def _short(issue: str) -> str:
    """Version courte d'une alerte pour le tableau compact (détail au survol de la case)."""
    issue = re.sub(r"\s*\(méd\. [^)]*\)", "", issue)
    issue = issue.replace("trop rapide ", "").replace("fait à ", "")
    return re.sub(r"pb ouverts?", "pb", issue)


def _alerts(cells: list[DayCell], hebdo: DayCell, days) -> str:
    """Alertes courtes : incohérences, antidatés, non clôturés (jour par jour, puis hebdo)."""
    flagged = (JAUNE, BLEU, ORANGE)
    items = []
    for day, cell in zip(days, cells, strict=False):
        if cell.color in flagged:
            text = ", ".join(_short(i) for i in cell.issues)
            items.append(f"<b>{JOURS_LONG[day.weekday()]}</b> {escape(text)}")
    if hebdo.color in flagged:
        items.append(f"<b>H</b> {escape(', '.join(_short(i) for i in hebdo.issues))}")
    if len(items) > 3:
        items = items[:3] + [f"+{len(items) - 3}"]
    return " · ".join(items) or "&nbsp;"


def _spacer(width: int = 10) -> str:
    return f'<td width="{width}" style="font-size:0;line-height:0">&nbsp;</td>'


def _region_rows(name: str, summary: str, colspan: int) -> str:
    return (
        f'<tr><td colspan="{colspan}" style="padding-top:8px;font-size:0;line-height:0">&nbsp;'
        "</td></tr>"
        f'<tr><td colspan="{colspan}" bgcolor="{SOFT}" style="padding:5px 6px;{FONT}'
        f'font-size:12px;font-weight:bold;color:{INK}">{escape(name)} '
        f'<span style="font-weight:normal;color:{MUTED}">{summary}</span></td></tr>'
    )


def _inspections(report: WeeklyReport) -> str:
    view = report.current
    small = f"{FONT}font-size:11px;font-weight:bold;"
    head = f'<td style="{FONT}font-size:11px;color:{MUTED};padding:0 4px">Parc</td>'
    for d in view.days:
        color = INK if d in view.open_days else "#BBBBBB"
        head += (
            f'<td width="26" align="center" style="{small}color:{color}">{JOURS[d.weekday()]}'
            f'<br><span style="font-weight:normal;font-size:9px">{d:%d}</span></td>'
        )
    head += _spacer() + f'<td width="26" align="center" style="{small}color:{INK}">H</td>'
    head += f'<td style="{FONT}font-size:11px;color:{MUTED};padding-left:8px">Alertes</td>'
    rows = [f"<tr>{head}</tr>"]
    for region in report.referentiel.regions:
        c = view.counts(region.parcs)
        summary = (
            f"— journaliers {c['done']}/{c['expected']} · hebdos {c['hebdos']}/"
            f"{len(region.parcs)}"
            + (f" · {c['incoherents']} incohérent(s)" if c["incoherents"] else "")
            + (f" · {c['incomplets']} non clôturé(s)" if c["incomplets"] else "")
        )
        rows.append(_region_rows(region.nom, summary, 11))
        for parc in region.parcs:
            cells = view.day_cells[parc]
            hebdo = view.hebdo[parc]
            rows.append(
                f'<tr><td width="74" style="padding:0 4px;{FONT}font-size:12px;font-weight:bold;'
                f'color:{INK};white-space:nowrap">{escape(parc)}</td>'
                + "".join(_status_cell(c) for c in cells)
                + _spacer()
                + _status_cell(hebdo)
                + f'<td style="padding:0 0 0 8px;{FONT}font-size:11px;color:#6B4A00;'
                f'line-height:14px">{_alerts(cells, hebdo, view.days)}</td></tr>'
            )
    return _row(f"<table {TABLE}>{''.join(rows)}</table>")


def _legend_cells(items: list[tuple[str, str, str, str]]) -> str:
    """(fond, texte, symbole, libellé) → une ligne de légende."""
    parts = []
    for bg, fg, symbol, text in items:
        parts.append(
            f'<td bgcolor="{bg}" width="18" height="18" align="center" style="{FONT}'
            f'font-size:11px;font-weight:bold;color:{fg}">{escape(symbol)}</td>'
            f'<td style="{FONT}font-size:11px;color:{MUTED};padding:0 10px 0 4px;'
            f'white-space:nowrap">{escape(text)}</td>'
        )
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td height="8" style="font-size:0;line-height:0">&nbsp;</td></tr></table>'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0">'
        f"<tr>{''.join(parts)}</tr></table>"
    )


def _legend(report: WeeklyReport) -> str:
    items = []
    for color in (VERT, JAUNE, ORANGE, ROUGE, BLEU, GRIS):
        bg, fg = CELL[color]
        label = STATE_LABELS[color]
        if color == GRIS:
            label += f" (< {report.opening_ratio:.0%} du réseau)"
        items.append((bg, fg, DayCell(color).symbol, label))
    median = report.current.journalier_median
    median_txt = f", soit {fmt_duration(median)} cette semaine" if median else ""
    note = _text(
        "<b>Incohérent</b> : « 3 min » = trop rapide (sous 40 % de la durée médiane du "
        f"réseau{median_txt}) ; « 02h00 » = heure inhabituelle ; « anticipé » = commencé avant "
        "le jour contrôlé ; « scores x→y » = plusieurs contrôles différents le même jour ; "
        "« 100 % malgré N pb » = tout conforme alors que le parc a des jeux en panne ou des "
        "actions signalées depuis moins de 14 jours. <b>Antidaté</b> : rempli après le jour "
        "contrôlé (« saisi le »). Détail au survol de chaque case."
    )
    return _row(_legend_cells(items) + note)


def _num(value: int, bad: bool = False) -> str:
    if not value:
        return '<span style="color:#BBBBBB">0</span>'
    return f'<span style="color:{"#C62828" if bad else INK}">{value}</span>'


def _delta(cur: int, prev: int) -> str:
    diff = cur - prev
    if diff == 0:
        return '<span style="color:#888888">=</span>'
    color = TONE["bad"] if diff > 0 else TONE["good"]
    return f'<span style="color:{color}">{"▲" if diff > 0 else "▼"} {abs(diff)}</span>'


def _actions(report: WeeklyReport) -> str:
    view, prev = report.current, report.previous
    total = view.counts(report.referentiel.parcs)
    intro = ""
    if total["rejet_pct"] is not None and total["rejet_pct"] >= 30:
        intro = (
            f'<table {TABLE}><tr><td bgcolor="#FFF4E5" style="padding:8px 10px;'
            f'border-left:4px solid #F28A2E;{FONT}font-size:12px;color:{INK};line-height:17px">'
            f"<b>{total['rejet_pct']:.0f} % des actions créées ont été rejetées</b> "
            f"({total['rejetees']} sur {total['creees']}) : elles n'étaient pas liées à un "
            "« Premier Signalement » et ne sont pas arrivées dans Smartsheet. Rappel à faire "
            "aux parcs en rouge dans la colonne « Rejet. ».</td></tr>"
            '<tr><td height="10" style="font-size:0;line-height:0">&nbsp;</td></tr></table>'
        )
    headers = [
        ("Parc", "left"),
        ("Créées", "center"),
        ("Transf.", "center"),
        ("Rejet.", "center"),
        ("À traiter", "center"),
        ("vs S-1", "center"),
        ("+30 j", "center"),
        ("Plus ancienne", "center"),
        ("Traitées", "center"),
    ]
    head = "".join(
        f'<td align="{a}" style="padding:4px;{FONT}font-size:11px;color:{MUTED};'
        f'border-bottom:2px solid {INK}">{h}</td>'
        for h, a in headers
    )
    rows = [f"<tr>{head}</tr>"]
    for region in report.referentiel.regions:
        c, p = view.counts(region.parcs), prev.counts(region.parcs)
        region_cells = [
            c["creees"],
            c["transferees"],
            c["rejetees"],
            c["a_traiter"],
            _delta(c["a_traiter"], p["a_traiter"]),
            "",
            "",
            c["traitees"],
        ]
        rows.append(
            f'<tr><td bgcolor="{SOFT}" style="padding:5px 4px;{FONT}font-size:12px;'
            f'font-weight:bold">{escape(region.nom)}</td>'
            + "".join(
                f'<td bgcolor="{SOFT}" align="center" style="{FONT}font-size:12px;'
                f'font-weight:bold">{v}</td>'
                for v in region_cells
            )
            + "</tr>"
        )
        for parc in sorted(region.parcs, key=lambda x: (-view.stats[x].a_traiter, x)):
            s, ps = view.stats[parc], prev.stats[parc]
            cells = [
                _num(s.creees),
                _num(s.transferees),
                _num(s.rejetees, bad=s.rejetees >= max(3, s.creees / 2)),
                f"<b>{s.a_traiter}</b>" if s.a_traiter else _num(0),
                _delta(s.a_traiter, ps.a_traiter),
                _num(s.anciennes, bad=True),
                f"{s.plus_ancienne:%d/%m/%y}" if s.plus_ancienne else "-",
                _num(s.traitees_semaine),
            ]
            rows.append(
                f'<tr><td style="padding:3px 4px;border-bottom:1px solid {LINE};{FONT}'
                f'font-size:12px">{escape(parc)}</td>'
                + "".join(
                    f'<td align="center" style="border-bottom:1px solid {LINE};{FONT}'
                    f'font-size:12px">{v}</td>'
                    for v in cells
                )
                + "</tr>"
            )
    note = _text(
        "Créées / Transf. / Rejet. : actions Mitti de la semaine. À traiter : lignes [AC-…] "
        "non cochées « Statut Mgr » dans le Smartsheet Travaux en fin de semaine ; +30 j : "
        "signalées il y a plus de 30 jours. Traitées : lignes terminées (date de Fin) dans la "
        "semaine."
    )
    return _row(intro + f"<table {TABLE}>{''.join(rows)}</table>{note}")


SHORT_GAMES = {
    "kartings": "Karting",
    "air hockey": "Air hockey",
    "machines à pinces avec peluches": "Pinces peluches",
    "machines à pinces sans peluches": "Pinces sans pel.",
    "jeux avec marteau": "Marteau",
    "carrousels": "Carrousel",
    "babyfoots": "Baby-foot",
    "flippers": "Flipper",
    '"zones tap-tap" (cibles)': "Tap-Tap",
    "kid challenge": "Kid Challenge",
    "néo expérience": "Néo Exp.",
}
GAME_CELL = {
    OK: ("#C9EBCB", "#1E6B2A"),
    MAINTENANCE: ("#FFE08A", "#7A5200"),
    PANNE: ("#F6B3B3", "#9B1111"),
}


def short_game(name: str) -> str:
    return SHORT_GAMES.get(name.lower(), name if len(name) <= 14 else name[:13] + ".")


def _rate_txt(pair: tuple[int, int]) -> str:
    value = rate(pair)
    return "-" if value is None else f"{value:.0f} %"


def _game_cell(state, cur: tuple[int, int], prev: tuple[int, int], width: int) -> str:
    """Case parc × machine : fond = état au dernier hebdo, texte = taux de panne T (T-1)."""
    if state is None and not cur[1] and not prev[1]:
        return (
            f'<td width="{width}" align="center" bgcolor="#FAFAFA" style="border:2px solid '
            f'#FFFFFF;{FONT}font-size:11px;color:#CCCCCC">–</td>'
        )
    bg, fg = GAME_CELL.get(state.state if state else None, ("#EFEFEF", "#666666"))
    second = f"({_rate_txt(prev)})"
    if state and state.state == PANNE and state.weeks_down > 1:
        second = f"<b>{state.weeks_down} sem.</b>"
    title = escape(
        f"{STATE_TXT.get(state.state, '?') if state else 'pas d hebdo récent'} ; "
        f"taux de panne {_rate_txt(cur)} sur {cur[1]} hebdo(s), trimestre précédent "
        f"{_rate_txt(prev)} sur {prev[1]}"
        + (f" ; {state.comment}" if state and state.comment else "")
    )
    return (
        f'<td width="{width}" height="30" align="center" bgcolor="{bg}" title="{title}" '
        f'style="border:2px solid #FFFFFF;{FONT}color:{fg};line-height:12px">'
        f'<span style="font-size:11px;font-weight:bold;{"opacity:.55;" if not cur[0] else ""}'
        f'color:{fg if cur[0] else "#6E9C72"}">{_rate_txt(cur)}</span><br>'
        f'<span style="font-size:9px">{second}</span></td>'
    )


STATE_TXT = {OK: "OK et en service", MAINTENANCE: "en service mais maintenance", PANNE: "EN PANNE"}


def _machines(report: WeeklyReport) -> str:
    machines = sorted(report.machines, key=lambda m: short_game(m).lower())
    if not machines:
        return _row(
            _text(
                "Aucune réponse « jeux électroniques » trouvée dans les hebdos "
                "(vérifier SAFETYSYNC_HEBDO_TEMPLATES).",
                12,
                MUTED,
                0,
            )
        )
    cur, prev = report.breakdowns_cur, report.breakdowns_prev
    width = max(40, min(56, (WIDTH - 48 - 74 - 56) // len(machines)))
    head = f'<td style="{FONT}font-size:11px;color:{MUTED};padding:0 4px">Parc</td>'
    head += "".join(
        f'<td width="{width}" align="center" valign="bottom" style="{FONT}font-size:9px;'
        f'font-weight:bold;color:{INK};line-height:11px;padding-bottom:3px">'
        f"{escape(short_game(m))}</td>"
        for m in machines
    )
    head += (
        f'<td width="56" align="center" valign="bottom" style="{FONT}font-size:9px;'
        f'font-weight:bold;color:{INK};padding-bottom:3px">Parc</td>'
    )
    colspan = len(machines) + 2

    def total_cell(c: tuple[int, int], p: tuple[int, int], bold: bool = True) -> str:
        weight = "bold" if bold else "normal"
        return (
            f'<td width="56" align="center" bgcolor="{SOFT}" style="border:2px solid #FFFFFF;'
            f'{FONT}line-height:12px"><span style="font-size:11px;font-weight:{weight}">'
            f'{_rate_txt(c)}</span><br><span style="font-size:9px;color:{MUTED}">'
            f"({_rate_txt(p)})</span></td>"
        )

    # Ligne « Réseau » : taux par machine sur tous les parcs
    network = (
        f'<tr><td style="padding:0 4px;{FONT}font-size:12px;font-weight:bold;color:{BRAND};'
        f'white-space:nowrap">Réseau</td>'
    )
    for m in machines:
        c, p = sum_counts(cur, game=m), sum_counts(prev, game=m)
        down = sum(
            1
            for states in report.current.games.values()
            if states.get(m) and states[m].state == PANNE
        )
        bg = "#F6B3B3" if down else SOFT
        network += (
            f'<td width="{width}" height="30" align="center" bgcolor="{bg}" style="border:2px '
            f'solid #FFFFFF;{FONT}line-height:12px"><span style="font-size:11px;'
            f'font-weight:bold">{_rate_txt(c)}</span><br><span style="font-size:9px;'
            f'color:{MUTED}">({_rate_txt(p)})</span></td>'
        )
    network += total_cell(sum_counts(cur), sum_counts(prev)) + "</tr>"
    rows = [f"<tr>{head}</tr>", network]

    for region in report.referentiel.regions:
        down_now = sum(
            1
            for parc in region.parcs
            for g in report.current.games.get(parc, {}).values()
            if g.state == PANNE
        )
        reg_cur = (
            sum(sum_counts(cur, site=p)[0] for p in region.parcs),
            sum(sum_counts(cur, site=p)[1] for p in region.parcs),
        )
        summary = f"— {down_now} jeu(x) en panne · taux {_rate_txt(reg_cur)}"
        rows.append(_region_rows(region.nom, summary, colspan))
        for parc in region.parcs:
            states = report.current.games.get(parc, {})
            cells = "".join(
                _game_cell(
                    states.get(m),
                    cur.get((parc, m), (0, 0)),
                    prev.get((parc, m), (0, 0)),
                    width,
                )
                for m in machines
            )
            rows.append(
                f'<tr><td width="74" style="padding:0 4px;{FONT}font-size:12px;font-weight:bold;'
                f'color:{INK};white-space:nowrap">{escape(parc)}</td>{cells}'
                + total_cell(sum_counts(cur, site=parc), sum_counts(prev, site=parc), False)
                + "</tr>"
            )
    legend = _legend_cells(
        [
            (*GAME_CELL[OK], "✓", "OK au dernier hebdo"),
            (*GAME_CELL[MAINTENANCE], "!", "en service mais maintenance"),
            (*GAME_CELL[PANNE], "✗", "en panne"),
            ("#FAFAFA", "#CCCCCC", "–", "machine absente"),
        ]
    )
    note = _text(
        f"Dans chaque case : taux de panne du {report.quarters[1]} (en cours) puis, entre "
        f"parenthèses, du {report.quarters[0]}. Taux de panne = réponses « En Panne » / "
        "réponses à la question dans les hebdos terminés (« En Service mais Maintenance » "
        "n'est pas une panne). « 3 sem. » = en panne depuis 3 hebdos consécutifs."
    )
    return _row(f"<table {TABLE}>{''.join(rows)}</table>" + legend + note)


def _notes(report: WeeklyReport) -> str:
    if not report.warnings:
        return ""
    items = "".join(f"<br>• {escape(w)}" for w in report.warnings)
    return _row(_text(f"<b>Notes techniques</b>{items}", top=0), "20px 24px 0")


def _footer() -> str:
    return _row(
        f'<table {TABLE}><tr><td style="border-top:1px solid {LINE};padding-top:10px;{FONT}'
        f'font-size:11px;color:{MUTED}">Rapport automatique Safety-Sync (Mitti + Smartsheet), '
        "envoyé chaque lundi. Données du lundi au dimanche de la semaine écoulée.</td></tr>"
        "</table>",
        "24px 24px 20px",
    )
