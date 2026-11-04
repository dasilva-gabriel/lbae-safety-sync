"""Collecte des données du rapport hebdomadaire (Mitti + Smartsheet)."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from safetysync.config import ReportSettings, SyncSettings
from safetysync.mitti import (
    STATUS_CANT_DO,
    STATUS_TODO,
    MittiAPI,
    task_site,
    task_status_id,
)
from safetysync.parcs import Referentiel, load_referentiel
from safetysync.report.analysis import (
    NOT_CONFIGURED,
    ActionStats,
    DayCell,
    GameState,
    Inspection,
    Priority,
    day_cell,
    game_states,
    hebdo_cell,
    is_elec,
    opening_days,
    priority,
    week_days,
)
from safetysync.smartsheet import parse_date
from safetysync.transfer import SheetCache

logger = logging.getLogger(__name__)


@dataclass
class WeeklyReport:
    monday: date
    days: list[date]
    open_days: set[date]
    referentiel: Referentiel
    day_cells: dict[str, list[DayCell]] = field(default_factory=dict)
    hebdo: dict[str, DayCell] = field(default_factory=dict)
    games: dict[str, dict[str, GameState]] = field(default_factory=dict)
    game_names: list[str] = field(default_factory=list)
    stats: dict[str, ActionStats] = field(default_factory=dict)
    priorities: list[Priority] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    priority_top: int = 10
    opening_ratio: float = 0.2

    @property
    def sunday(self) -> date:
        return self.days[-1]


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
    days = week_days(monday)
    start = datetime.combine(monday, time.min, tz)
    end = start + timedelta(days=7)
    warnings: list[str] = []
    referentiel = load_referentiel(settings.parcs_file)
    if not referentiel.regions:
        warnings.append(
            f"Référentiel des parcs absent ({settings.parcs_file}) : parcs regroupés "
            "dans « Autres parcs »."
        )

    site_names = {s["id"]: (s.get("name") or "").strip().upper() for s in mitti.feed_sites()}

    def to_inspection(raw: dict) -> Inspection | None:
        site = site_names.get(raw.get("site_id") or "", "")
        if not site:
            return None
        return Inspection.from_feed(raw, site, tz)

    # --- Journaliers de la semaine
    journaliers: list[Inspection] = []
    no_site = 0
    if settings.journalier_template_ids:
        for raw in mitti.feed_inspections(
            settings.journalier_template_ids, start - timedelta(days=7)
        ):
            insp = to_inspection(raw)
            if insp is None:
                no_site += 1
            elif monday <= insp.day <= days[-1]:
                journaliers.append(insp)
    else:
        warnings.append("SAFETYSYNC_JOURNALIER_TEMPLATE_IDS non renseigné : journaliers ignorés.")

    # --- Hebdos (semaine + historique pour les jeux électroniques)
    hebdos: list[Inspection] = []
    items: dict[str, list[dict]] = defaultdict(list)
    if settings.hebdo_template_ids:
        since = start - timedelta(weeks=settings.elec_history_weeks)
        for raw in mitti.feed_inspections(settings.hebdo_template_ids, since):
            insp = to_inspection(raw)
            if insp is None:
                no_site += 1
            elif since.date() <= insp.day <= days[-1]:
                hebdos.append(insp)
        for item in mitti.feed_inspection_items(settings.hebdo_template_ids, since):
            if is_elec(item.get("label") or "", settings.elec_prefix):
                items[item.get("audit_id") or ""].append(item)
    else:
        warnings.append("SAFETYSYNC_HEBDO_TEMPLATE_IDS non renseigné : hebdos ignorés.")
    if no_site:
        warnings.append(f"{no_site} inspection(s) sans site Mitti : non comptées.")

    # --- Actions Mitti
    stats: dict[str, ActionStats] = defaultdict(ActionStats)
    for task in mitti.iter_actions(created_from=start, created_to=end):
        site = task_site(task)
        if not site:
            continue
        s = stats[site]
        s.creees += 1
        status = task_status_id(task)
        if status == STATUS_CANT_DO:
            s.rejetees += 1
        elif status != STATUS_TODO:
            s.transferees += 1
    since_open = datetime.now(tz) - timedelta(days=sync_settings.lookback_days)
    for task in mitti.iter_actions(status_ids=[STATUS_TODO], created_from=since_open):
        if site := task_site(task):
            stats[site].ouvertes += 1

    # --- Smartsheet : stock d'actions à traiter
    if sheets is not None:
        old_limit = days[-1] - timedelta(days=settings.old_action_days)
        for site in sorted(sheets.by_site):
            try:
                sheet = sheets.for_site(site)
            except Exception as exc:
                warnings.append(f"Feuille Travaux {site} illisible : {exc}")
                continue
            if sheet is None:
                continue
            s = stats[site] if site in stats or site in referentiel.parcs else None
            if s is None:
                # Feuille d'un parc non référencé et sans activité : ignorée.
                continue
            for _, row in sheet.ac_rows():
                if sheet.is_done(row):
                    end_day = parse_date(row.get(sheet.columns["end"]))
                    if end_day and monday <= end_day <= days[-1]:
                        s.traitees_semaine += 1
                    continue
                s.a_traiter += 1
                reported = parse_date(row.get(sheet.columns["start"])) or (
                    row.created_at.astimezone(tz).date() if row.created_at else None
                )
                if reported:
                    if reported < old_limit:
                        s.anciennes += 1
                    if s.plus_ancienne is None or reported < s.plus_ancienne:
                        s.plus_ancienne = reported

    # --- Assemblage
    seen = {i.site for i in journaliers} | {i.site for i in hebdos} | set(stats)
    referentiel = referentiel.with_unknown(seen)
    parcs = referentiel.parcs
    open_days = opening_days(days, journaliers, [p for p in parcs], settings.opening_ratio)

    by_site_day: dict[tuple[str, date], list[Inspection]] = defaultdict(list)
    for insp in sorted(journaliers, key=lambda i: i.day):
        by_site_day[(insp.site, insp.day)].append(insp)
    week_hebdos: dict[str, list[Inspection]] = defaultdict(list)
    for insp in hebdos:
        if monday <= insp.day <= days[-1]:
            week_hebdos[insp.site].append(insp)

    report = WeeklyReport(
        monday=monday,
        days=days,
        open_days=open_days,
        referentiel=referentiel,
        warnings=warnings,
        priority_top=settings.priority_top,
        opening_ratio=settings.opening_ratio,
    )
    report.games = game_states(items, hebdos, settings.elec_prefix, days[-1])
    names: dict[str, None] = {}
    for states in report.games.values():
        names.update(dict.fromkeys(states))
    report.game_names = list(names)

    for parc in parcs:
        if settings.journalier_template_ids:
            report.day_cells[parc] = [
                day_cell(by_site_day[(parc, d)], d in open_days, settings.min_duration_seconds)
                for d in days
            ]
        else:
            report.day_cells[parc] = [NOT_CONFIGURED] * len(days)
        report.hebdo[parc] = (
            hebdo_cell(week_hebdos[parc]) if settings.hebdo_template_ids else NOT_CONFIGURED
        )
        report.stats[parc] = stats.get(parc, ActionStats())
        report.priorities.append(
            priority(
                parc,
                report.day_cells[parc],
                report.hebdo[parc],
                report.games.get(parc, {}),
                report.stats[parc],
            )
        )
    report.priorities.sort(key=lambda p: (-p.score, p.site))
    logger.info(
        "Rapport : %s parc(s), %s journalier(s), %s hebdo(s), jours ouverts %s",
        len(parcs),
        len(journaliers),
        len(hebdos),
        sorted(d.isoformat() for d in open_days),
    )
    return report
