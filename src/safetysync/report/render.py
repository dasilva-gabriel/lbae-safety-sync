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
    ROUGE,
    VERT,
    DayCell,
    Kpi,
    fmt_duration,
)
from safetysync.report.collect import MachineRate, WeeklyReport

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
            "Pannes, contrôles manquants ou suspects, stock d'actions.",
        ),
        _top(report),
        _section(
            "Inspections de la semaine",
            "H = hebdo, puis un journalier par jour. Cliquez une case pour ouvrir le rapport.",
        ),
        _inspections(report),
        _legend(report),
        _section("Focus actions", "Actions Mitti créées dans la semaine et stock Smartsheet."),
        _actions(report),
        _section(
            "Focus jeux électroniques",
            f"Taux de panne = part des réponses « En Panne » aux hebdos ({report.quarters[0]}, "
            f"puis {report.quarters[1]} en cours).",
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
            f'font-size:10px;color:{MUTED}">score<br>'
            f'<b style="font-size:15px;color:{INK}">{p.score:g}</b></td></tr>'
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
    items = []
    if hebdo.color == JAUNE:
        items.append(f"<b>H</b> {escape(', '.join(_short(i) for i in hebdo.issues))}")
    for day, cell in zip(days, cells, strict=False):
        if cell.color == JAUNE:
            text = ", ".join(_short(i) for i in cell.issues)
            items.append(f"<b>{JOURS_LONG[day.weekday()]}</b> {escape(text)}")
    if len(items) > 3:
        items = items[:3] + [f"+{len(items) - 3}"]
    return " · ".join(items) or "&nbsp;"


def _inspections(report: WeeklyReport) -> str:
    view = report.current
    small = f"{FONT}font-size:11px;font-weight:bold;"
    head = f'<td style="{FONT}font-size:11px;color:{MUTED};padding:0 4px">Parc</td>'
    head += f'<td width="26" align="center" style="{small}color:{INK}">H</td>'
    for d in view.days:
        color = INK if d in view.open_days else "#BBBBBB"
        head += (
            f'<td width="26" align="center" style="{small}color:{color}">{JOURS[d.weekday()]}'
            f'<br><span style="font-weight:normal;font-size:9px">{d:%d}</span></td>'
        )
    head += f'<td style="{FONT}font-size:11px;color:{MUTED};padding-left:8px">Alertes</td>'
    rows = [f"<tr>{head}</tr>"]
    for region in report.referentiel.regions:
        counts = view.counts(region.parcs)
        summary = (
            f"journaliers {counts['done']}/{counts['expected']} · hebdos "
            f"{counts['hebdos']}/{len(region.parcs)}"
            + (f" · {counts['suspects']} suspect(s)" if counts["suspects"] else "")
        )
        rows.append(
            f'<tr><td colspan="10" style="padding-top:8px;font-size:0;line-height:0">&nbsp;'
            f"</td></tr>"
            f'<tr><td colspan="10" bgcolor="{SOFT}" style="padding:5px 6px;{FONT}font-size:12px;'
            f'font-weight:bold;color:{INK}">{escape(region.nom)} '
            f'<span style="font-weight:normal;color:{MUTED}">— {summary}</span></td></tr>'
        )
        for parc in region.parcs:
            cells = view.day_cells[parc]
            hebdo = view.hebdo[parc]
            rows.append(
                f'<tr><td width="74" style="padding:0 4px;{FONT}font-size:12px;font-weight:bold;'
                f'color:{INK};white-space:nowrap">{escape(parc)}</td>'
                + _status_cell(hebdo)
                + "".join(_status_cell(c) for c in cells)
                + f'<td style="padding:0 0 0 8px;{FONT}font-size:11px;color:#6B4A00;'
                f'line-height:14px">{_alerts(cells, hebdo, view.days)}</td></tr>'
            )
    return _row(f"<table {TABLE}>{''.join(rows)}</table>")


def _legend(report: WeeklyReport) -> str:
    items = [
        (VERT, "fait, cohérent"),
        (JAUNE, "suspect / incomplet"),
        (ROUGE, "manquant"),
        (BLEU, "anticipé"),
        (GRIS, f"fermé (< {report.opening_ratio:.0%} du réseau)"),
    ]
    parts = []
    for color, text in items:
        bg, fg = CELL[color]
        parts.append(
            f'<td bgcolor="{bg}" width="18" height="18" align="center" style="{FONT}'
            f'font-size:11px;font-weight:bold;color:{fg}">{DayCell(color).symbol}</td>'
            f'<td style="{FONT}font-size:11px;color:{MUTED};padding:0 12px 0 4px;'
            f'white-space:nowrap">{escape(text)}</td>'
        )
    median = report.current.journalier_median
    median_txt = f", soit {fmt_duration(median)} cette semaine" if median else ""
    note = _text(
        "<b>Alertes</b> : « 3 min » = contrôle trop rapide (sous 40 % de la durée médiane du "
        f"réseau{median_txt}) ; « 02h00 » = fait à une heure inhabituelle ; « saisi le » = "
        "rempli après coup ; « scores x→y » = plusieurs contrôles le même jour ; « 100 % malgré "
        "N pb » = tout conforme alors que le parc a des jeux en panne ou des actions signalées "
        "depuis moins de 14 jours. Détail au survol de chaque case."
    )
    return _row(
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td height="8" style="font-size:0;line-height:0">&nbsp;</td></tr></table>'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0">'
        f"<tr>{''.join(parts)}</tr></table>{note}"
    )


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


def _rate_bar(rate: float | None, n: int) -> str:
    if rate is None:
        return f'<span style="{FONT}font-size:11px;color:#BBBBBB">pas de donnée</span>'
    width = min(max(int(round(rate)), 1), 100)
    color = "#C62828" if rate >= 20 else "#F28A2E" if rate >= 10 else "#43A047"
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td width="80" bgcolor="#EEEEEE" style="font-size:0;line-height:0">'
        f'<table role="presentation" width="{width}%" cellpadding="0" cellspacing="0" '
        f'border="0"><tr><td bgcolor="{color}" height="8" style="font-size:0;line-height:0">'
        "&nbsp;</td></tr></table></td>"
        f'<td style="padding-left:6px;{FONT}font-size:12px;font-weight:bold;color:{INK};'
        f'white-space:nowrap">{rate:.0f} %<span style="font-weight:normal;font-size:10px;'
        f'color:{MUTED}"> sur {n}</span></td></tr></table>'
    )


def _machines(report: WeeklyReport) -> str:
    if not report.machines:
        return _row(
            _text(
                "Aucune réponse « jeux électroniques » trouvée dans les hebdos "
                "(vérifier SAFETYSYNC_HEBDO_TEMPLATE_IDS).",
                12,
                MUTED,
                0,
            )
        )
    q_prev, q_cur = report.quarters
    head = "".join(
        f'<td style="padding:4px;{FONT}font-size:11px;color:{MUTED};'
        f'border-bottom:2px solid {INK}">{h}</td>'
        for h in ("Machine", q_prev, f"{q_cur} (en cours)", "Évol.", "En panne aujourd'hui")
    )
    rows = [f"<tr>{head}</tr>"]
    cell = f"border-bottom:1px solid {LINE};{FONT}"
    for m in report.machines:
        prev_rate, cur_rate = MachineRate.rate(m.prev), MachineRate.rate(m.cur)
        if prev_rate is None or cur_rate is None:
            evol = '<span style="color:#888888">-</span>'
        else:
            diff = cur_rate - prev_rate
            color = TONE["bad"] if diff > 1 else TONE["good"] if diff < -1 else "#888888"
            arrow = "▲" if diff > 1 else "▼" if diff < -1 else "="
            evol = f'<span style="color:{color};font-weight:bold">{arrow} {diff:+.0f} pts</span>'
        down = (
            ", ".join(f"<b>{escape(p)}</b>{f' ({w} sem.)' if w > 1 else ''}" for p, w in m.down_now)
            or '<span style="color:#BBBBBB">aucun</span>'
        )
        if m.maint_now:
            down += (
                f'<br><span style="color:{MUTED}">à maintenir : '
                f"{escape(', '.join(m.maint_now))}</span>"
            )
        rows.append(
            f'<tr><td width="140" style="padding:6px 4px;{cell}font-size:12px;'
            f'font-weight:bold">{escape(m.name)}</td>'
            f'<td width="140" style="{cell}">{_rate_bar(prev_rate, m.prev[1])}</td>'
            f'<td width="140" style="{cell}">{_rate_bar(cur_rate, m.cur[1])}</td>'
            f'<td width="70" style="{cell}font-size:11px">{evol}</td>'
            f'<td style="{cell}font-size:11px;color:{INK};line-height:15px;padding:6px 0">'
            f"{down}</td></tr>"
        )
    return _row(
        f"<table {TABLE}>{''.join(rows)}</table>"
        + _text(
            "Barre verte < 10 %, orange 10–20 %, rouge ≥ 20 %. « sur N » = nombre de "
            "réponses à la question sur le trimestre (tous parcs)."
        )
    )


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
