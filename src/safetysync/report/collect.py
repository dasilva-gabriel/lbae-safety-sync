"""Collecte des données du rapport hebdomadaire (Mitti + Smartsheet).

On lit la semaine du rapport ET la précédente (tendances « vs S-1 »), ainsi que les
hebdos du trimestre précédent et du trimestre en cours (taux de panne des jeux).
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from safetysync.config import ReportSettings, SyncSettings
from safetysync.mitti import STATUS_CANT_DO, STATUS_TODO, MittiAPI, task_site, task_status_id
from safetysync.parcs import Referentiel, referentiel_from_sites
from safetysync.report.analysis import (
    BLEU,
    DONE_COLORS,
    JAUNE,
    NOT_CONFIGURED,
    ORANGE,
    PANNE,
    ActionStats,
    DayCell,
    GameState,
    Inspection,
    Kpi,
    Priority,
    Thresholds,
    breakdown_counts,
    day_cell,
    game_states,
    hebdo_cell,
    is_elec,
    opening_days,
    previous_quarter_start,
    priority,
    quarter_label,
    quarter_start,
    thresholds,
    week_days,
)
from safetysync.smartsheet import parse_date
from safetysync.transfer import SheetCache

logger = logging.getLogger(__name__)


@dataclass
class SheetLine:
    """Ligne [AC-xxx] d'une feuille Travaux, réduite à ce qui sert au rapport."""

    reported: date | None
    done: bool
    end: date | None

    def open_on(self, day: date) -> bool:
        """Encore à traiter à la fin de `day` (une ligne cochée sans Fin compte comme close)."""
        if self.reported and self.reported > day:
            return False
        if not self.done:
            return True
        return bool(self.end and self.end > day)


@dataclass
class WeekView:
    monday: date
    days: list[date]
    open_days: set[date]
    day_cells: dict[str, list[DayCell]] = field(default_factory=dict)
    hebdo: dict[str, DayCell] = field(default_factory=dict)
    stats: dict[str, ActionStats] = field(default_factory=dict)
    games: dict[str, dict[str, GameState]] = field(default_factory=dict)
    journalier_median: int | None = None
    synthesis: dict[str, str] = field(default_factory=dict)  # synthèse du dernier hebdo

    @property
    def sunday(self) -> date:
        return self.days[-1]

    def note_journaliers(self, parc: str) -> float | None:
        """Note moyenne des journaliers clôturés de la semaine (RAP-11)."""
        scores = [c.score for c in self.day_cells.get(parc, []) if c.score is not None]
        return sum(scores) / len(scores) if scores else None

    def note_hebdo(self, parc: str) -> float | None:
        return self.hebdo.get(parc, NOT_CONFIGURED).score

    def counts(self, parcs: list[str]) -> dict:
        expected = done = incoherents = incomplets = 0
        for parc in parcs:
            cells = [*self.day_cells.get(parc, []), self.hebdo.get(parc, NOT_CONFIGURED)]
            for day, cell in zip(self.days, self.day_cells.get(parc, []), strict=False):
                if day in self.open_days:
                    expected += 1
                    done += cell.color in DONE_COLORS
            incoherents += sum(1 for c in cells if c.color in (JAUNE, BLEU))
            incomplets += sum(1 for c in cells if c.color == ORANGE)
        hebdos = sum(1 for p in parcs if self.hebdo.get(p, NOT_CONFIGURED).color in DONE_COLORS)
        stats = [self.stats.get(p, ActionStats()) for p in parcs]
        creees = sum(s.creees for s in stats)
        rejetees = sum(s.rejetees for s in stats)
        return {
            "expected": expected,
            "done": done,
            "journaliers_pct": 100 * done / expected if expected else None,
            "hebdos": hebdos,
            "incoherents": incoherents,
            "incomplets": incomplets,
            "pannes": sum(
                1 for p in parcs for g in self.games.get(p, {}).values() if g.state == PANNE
            ),
            "creees": creees,
            "transferees": sum(s.transferees for s in stats),
            "rejetees": rejetees,
            "rejet_pct": 100 * rejetees / creees if creees else None,
            "a_traiter": sum(s.a_traiter for s in stats),
            "traitees": sum(s.traitees_semaine for s in stats),
        }


@dataclass
class WeeklyReport:
    referentiel: Referentiel
    current: WeekView
    previous: WeekView
    kpis: list[Kpi] = field(default_factory=list)
    priorities: list[Priority] = field(default_factory=list)
    # Jeux électroniques : machines vues, et (pannes, réponses) par (parc, machine)
    machines: list[str] = field(default_factory=list)
    breakdowns_prev: dict[tuple[str, str], tuple[int, int]] = field(default_factory=dict)
    breakdowns_cur: dict[tuple[str, str], tuple[int, int]] = field(default_factory=dict)
    quarters: tuple[str, str] = ("", "")  # (précédent, en cours)
    warnings: list[str] = field(default_factory=list)
    priority_top: int = 5
    opening_ratio: float = 0.2

    @property
    def monday(self) -> date:
        return self.current.monday

    @property
    def sunday(self) -> date:
        return self.current.sunday

    @property
    def days(self) -> list[date]:
        return self.current.days


def previous_monday(today: date) -> date:
    return today - timedelta(days=today.weekday() + 7)


def collect_weekly(
    mitti: MittiAPI,
    sheets: SheetCache | None,
    settings: ReportSettings,
    sync_settings: SyncSettings,
    monday: date,
) -> WeeklyReport:
    tz = settings.tz
    prev_monday = monday - timedelta(days=7)
    sunday = monday + timedelta(days=6)
    start = datetime.combine(prev_monday, time.min, tz)
    end = datetime.combine(sunday + timedelta(days=1), time.min, tz)
    warnings: list[str] = []

    referentiel = referentiel_from_sites(
        list(mitti.feed_sites()),
        region_level=settings.region_level,
        excluded=settings.excluded_sites,
    )
    excluded = {e.strip().upper() for e in settings.excluded_sites}
    j_ids = _templates(mitti, settings.journalier_templates, "journalier", warnings)
    h_ids = _templates(mitti, settings.hebdo_templates, "hebdo", warnings)

    def to_inspection(raw: dict) -> Inspection | None:
        site = referentiel.site_names.get(raw.get("site_id") or "", "")
        return Inspection.from_feed(raw, site, tz) if site else None

    # Trame utilisée par parc dans la semaine (pour repérer les anciennes versions)
    used_templates: dict[str, dict[str, tuple[date, str]]] = {"journalier": {}, "hebdo": {}}

    # --- Journaliers (2 semaines)
    journaliers: list[Inspection] = []
    no_site = 0
    if j_ids:
        for raw in mitti.feed_inspections(j_ids, start - timedelta(days=7)):
            insp = to_inspection(raw)
            if insp is None:
                no_site += 1
            elif prev_monday <= insp.day <= sunday:
                journaliers.append(insp)
                if insp.day >= monday:
                    _note_template(used_templates["journalier"], insp, raw)
    else:
        warnings.append("Aucune trame journalier trouvée (SAFETYSYNC_JOURNALIER_TEMPLATES).")

    # --- Hebdos + réponses « jeux électroniques » (trimestre précédent → semaine)
    q_prev = previous_quarter_start(sunday)
    since_day = min(q_prev, prev_monday - timedelta(weeks=settings.elec_history_weeks))
    since = datetime.combine(since_day, time.min, tz)
    hebdos: list[Inspection] = []
    items: dict[str, list[dict]] = defaultdict(list)
    synthesis: dict[str, str] = {}
    if h_ids:
        for raw in mitti.feed_inspections(h_ids, since):
            insp = to_inspection(raw)
            if insp is None:
                no_site += 1
            elif since_day <= insp.day <= sunday:
                hebdos.append(insp)
                if insp.day >= monday:
                    _note_template(used_templates["hebdo"], insp, raw)
        synthesis_label = settings.synthesis_label.strip().lower()
        for item in mitti.feed_inspection_items(h_ids, since):
            label = item.get("label") or ""
            if is_elec(label, settings.elec_prefix):
                items[item.get("audit_id") or ""].append(item)
            elif synthesis_label and label.strip().lower().startswith(synthesis_label):
                text = str(item.get("response") or "").strip()
                if text:
                    synthesis[item.get("audit_id") or ""] = text
    else:
        warnings.append("Aucune trame hebdo trouvée (SAFETYSYNC_HEBDO_TEMPLATES).")
    for kind, by_site in used_templates.items():
        warnings += _template_mismatches(kind, by_site)
    if no_site:
        warnings.append(f"{no_site} inspection(s) sans site Mitti : non comptées.")

    # --- Actions Mitti créées sur les 2 semaines
    created: dict[date, dict[str, ActionStats]] = {
        prev_monday: defaultdict(ActionStats),
        monday: defaultdict(ActionStats),
    }
    for task in mitti.iter_actions(created_from=start, created_to=end):
        site = task_site(task)
        created_day = _local_day(task.get("created_at"), tz)
        if not site or created_day is None:
            continue
        s = created[monday if created_day >= monday else prev_monday][site]
        s.creees += 1
        status = task_status_id(task)
        if status == STATUS_CANT_DO:
            s.rejetees += 1
        elif status != STATUS_TODO:
            s.transferees += 1
    open_todo: dict[str, int] = defaultdict(int)
    since_open = datetime.now(tz) - timedelta(days=sync_settings.lookback_days)
    for task in mitti.iter_actions(status_ids=[STATUS_TODO], created_from=since_open):
        if site := task_site(task):
            open_todo[site] += 1

    # --- Smartsheet : lignes [AC-xxx]
    lines: dict[str, list[SheetLine]] = defaultdict(list)
    if sheets is not None:
        for site in sorted(sheets.by_site):
            try:
                sheet = sheets.for_site(site)
            except Exception as exc:
                warnings.append(f"Feuille Travaux {site} illisible : {exc}")
                continue
            if sheet is None:
                continue
            for _, row in sheet.ac_rows():
                reported = parse_date(row.get(sheet.columns["start"])) or (
                    row.created_at.astimezone(tz).date() if row.created_at else None
                )
                end_day = parse_date(row.get(sheet.columns["end"]))
                lines[site].append(SheetLine(reported, sheet.is_done(row), end_day))

    # --- Référentiel final (parcs vus mais absents de l'arbre Mitti)
    seen = {i.site for i in journaliers} | {i.site for i in hebdos}
    for week_stats in created.values():
        seen |= set(week_stats)
    referentiel = referentiel.with_unknown(seen - excluded)
    parcs = referentiel.parcs

    # Notes des journaliers clôturés, par parc et par jour (écart avec le précédent, RAP-03)
    closed_scores: dict[str, dict[date, float]] = defaultdict(dict)
    for insp in journaliers:
        if insp.completed and insp.score is not None:
            closed_scores[insp.site].setdefault(insp.day, insp.score)

    def previous_score(parc: str, day: date):
        for back in range(1, 4):
            d = day - timedelta(days=back)
            if d in closed_scores.get(parc, {}):
                return closed_scores[parc][d], d
        return None

    def build(week_monday: date) -> WeekView:
        days = week_days(week_monday)
        week_j = [i for i in journaliers if days[0] <= i.day <= days[-1]]
        week_h = [i for i in hebdos if days[0] <= i.day <= days[-1]]
        view = WeekView(
            monday=week_monday,
            days=days,
            open_days=opening_days(days, week_j, parcs, settings.opening_ratio),
        )
        th_j = _thresholds(week_j, settings.min_duration_seconds, settings)
        th_h = _thresholds(week_h, settings.min_hebdo_duration_seconds, settings)
        view.journalier_median = th_j.median
        view.games = game_states(items, hebdos, settings.elec_prefix, days[-1])
        before = game_states(items, hebdos, settings.elec_prefix, days[0] - timedelta(days=1))

        by_day: dict[tuple[str, date], list[Inspection]] = defaultdict(list)
        for insp in sorted(
            week_j, key=lambda i: (i.day, i.started.timestamp() if i.started else 0)
        ):
            by_day[(insp.site, insp.day)].append(insp)
        by_site_h: dict[str, list[Inspection]] = defaultdict(list)
        for insp in week_h:
            by_site_h[insp.site].append(insp)

        old_limit = days[-1] - timedelta(days=settings.old_action_days)
        for parc in parcs:
            pannes_before = sum(1 for g in before.get(parc, {}).values() if g.state == PANNE)
            if j_ids:
                cells = []
                for d in days:
                    issues = 0
                    if settings.perfect_score_check:
                        recent = d - timedelta(days=settings.recent_issue_days)
                        issues = pannes_before + sum(
                            1
                            for ln in lines.get(parc, [])
                            if ln.reported
                            and recent <= ln.reported < d
                            and ln.open_on(d - timedelta(days=1))
                        )
                    cells.append(
                        day_cell(
                            by_day[(parc, d)],
                            d in view.open_days,
                            th_j,
                            issues,
                            previous_score(parc, d),
                        )
                    )
                view.day_cells[parc] = cells
            else:
                view.day_cells[parc] = [NOT_CONFIGURED] * 7
            view.hebdo[parc] = hebdo_cell(by_site_h[parc], th_h) if h_ids else NOT_CONFIGURED
            closed_h = sorted(
                (i for i in by_site_h[parc] if i.completed and i.id in synthesis),
                key=lambda i: i.day,
            )
            if closed_h:
                view.synthesis[parc] = synthesis[closed_h[-1].id]
            base = created[week_monday].get(parc) or ActionStats()
            s = ActionStats(base.creees, base.transferees, base.rejetees)
            for ln in lines.get(parc, []):
                if ln.open_on(days[-1]):
                    s.a_traiter += 1
                    if ln.reported and ln.reported < old_limit:
                        s.anciennes += 1
                    if ln.reported and (s.plus_ancienne is None or ln.reported < s.plus_ancienne):
                        s.plus_ancienne = ln.reported
                elif ln.done and ln.end and days[0] <= ln.end <= days[-1]:
                    s.traitees_semaine += 1
            view.stats[parc] = s
        return view

    current = build(monday)
    previous = build(prev_monday)
    for parc, n in open_todo.items():
        if parc in current.stats:
            current.stats[parc].ouvertes = n

    report = WeeklyReport(
        referentiel=referentiel,
        current=current,
        previous=previous,
        warnings=warnings,
        priority_top=settings.priority_top,
        opening_ratio=settings.opening_ratio,
    )
    report.kpis = build_kpis(current.counts(parcs), previous.counts(parcs), len(parcs))
    for parc in parcs:
        report.priorities.append(
            priority(
                parc,
                current.stats[parc],
                current.note_journaliers(parc),
                current.note_hebdo(parc),
            )
        )
    report.priorities.sort(key=lambda p: (-p.score, p.site))

    # --- Taux de panne par parc et par machine (trimestre précédent / en cours)
    q_cur = quarter_start(sunday)
    report.breakdowns_prev = breakdown_counts(
        items, hebdos, settings.elec_prefix, q_prev, q_cur - timedelta(days=1)
    )
    report.breakdowns_cur = breakdown_counts(items, hebdos, settings.elec_prefix, q_cur, sunday)
    report.quarters = (quarter_label(q_prev), quarter_label(q_cur))
    names = {g for _, g in report.breakdowns_prev} | {g for _, g in report.breakdowns_cur}
    for states in current.games.values():
        names |= set(states)
    report.machines = sorted(names)

    logger.info(
        "Rapport : %s parc(s), %s journalier(s) sur 2 semaines, %s hebdo(s), jours ouverts %s",
        len(parcs),
        len(journaliers),
        len(hebdos),
        sorted(d.isoformat() for d in current.open_days),
    )
    return report


def build_kpis(cur: dict, prev: dict, nb_parcs: int) -> list[Kpi]:
    """RAP-09 : indicateurs du bandeau « État du réseau », comparés à la semaine précédente."""

    def fmt_pct(v):
        return "-" if v is None else f"{v:.0f} %"

    return [
        Kpi(
            "Journaliers faits",
            fmt_pct(cur["journaliers_pct"]),
            cur["journaliers_pct"],
            prev["journaliers_pct"],
            "up",
            "%",
            f"{cur['done']}/{cur['expected']} attendus",
        ),
        Kpi("Hebdos faits", f"{cur['hebdos']}/{nb_parcs}", cur["hebdos"], prev["hebdos"], "up"),
        Kpi(
            "Contrôles incohérents",
            str(cur["incoherents"]),
            cur["incoherents"],
            prev["incoherents"],
            "down",
            "",
            f"{cur['incomplets']} non clôturés",
        ),
        Kpi("Jeux en panne", str(cur["pannes"]), cur["pannes"], prev["pannes"], "down"),
        Kpi(
            "Actions créées",
            str(cur["creees"]),
            cur["creees"],
            prev["creees"],
            None,
            "",
            f"{cur['transferees']} transférées",
        ),
        Kpi(
            "Taux de rejet",
            fmt_pct(cur["rejet_pct"]),
            cur["rejet_pct"],
            prev["rejet_pct"],
            "down",
            "%",
            f"{cur['rejetees']} rejetées",
        ),
        Kpi("Stock à traiter", str(cur["a_traiter"]), cur["a_traiter"], prev["a_traiter"], "down"),
        Kpi("Actions traitées", str(cur["traitees"]), cur["traitees"], prev["traitees"], "up"),
    ]


def _templates(mitti: MittiAPI, spec: str, kind: str, warnings: list[str]) -> list[str]:
    found = mitti.resolve_templates(spec)
    logger.info("Trames %s : %s", kind, ", ".join(f"{n} ({i})" for i, n in found) or "aucune")
    return [i for i, _ in found]


def _note_template(by_site: dict, insp: Inspection, raw: dict) -> None:
    name = raw.get("template_name") or raw.get("template_id") or ""
    if name and (insp.site not in by_site or by_site[insp.site][0] <= insp.day):
        by_site[insp.site] = (insp.day, name)


def _template_mismatches(kind: str, by_site: dict[str, tuple[date, str]]) -> list[str]:
    """Parcs dont le dernier contrôle de la semaine utilise une autre trame que la majorité."""
    names = Counter(name for _, name in by_site.values())
    if len(names) < 2:
        return []
    main = names.most_common(1)[0][0]
    return [
        f"Trame {kind} différente : {site} utilise « {name} » (réseau : « {main} »)."
        for site, (_, name) in sorted(by_site.items())
        if name != main
    ]


def _thresholds(inspections: list[Inspection], minimum: int, s: ReportSettings) -> Thresholds:
    return thresholds(
        inspections,
        minimum,
        s.suspicious_duration_ratio,
        s.usual_hour_start,
        s.usual_hour_end,
        s.score_jump_points,
    )


def _local_day(value, tz) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(tz).date()
    except ValueError:
        return None
