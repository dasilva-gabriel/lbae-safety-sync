"""Étapes 1 et 2 : transfert des actions Mitti « To do » vers les feuilles Travaux.

Règles (voir docs/fonctionnement.md) :
- SYNC-01 : actions « To do » créées depuis `lookback_days` jours, toutes pages lues.
- SYNC-02 : seule une action liée à une réponse « Premier Signalement » (ou En Panne,
  En Service mais Maintenance, Non OK…) est transférée : elle porte la photo et le
  commentaire de l'inspection. Les autres passent en « Can't do » avec un commentaire.
- SYNC-03 : la feuille cible est « Travaux … (CODE-PARC) », CODE-PARC = nom du site Mitti.
  Pas de feuille, ou pas de ligne « MAINTENANCE JEUX » : « Can't do » + commentaire.
- SYNC-04 : anti-doublon : si le n° AC-xxx est déjà dans la feuille, aucune ligne n'est
  ajoutée (on termine seulement la mise à jour de l'action).
- SYNC-05 : après transfert, l'action passe « In progress » avec un commentaire de suivi.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from safetysync.config import MittiSettings, SyncSettings
from safetysync.mitti import (
    STATUS_CANT_DO,
    STATUS_IN_PROGRESS,
    STATUS_TODO,
    MittiAPI,
    find_valid_reference,
    parse_dt,
    task_creator,
    task_site,
)
from safetysync.smartsheet import SheetRow, SmartsheetAPI, TravauxSheet, find_travaux_sheets

logger = logging.getLogger(__name__)

TAG = "[SAFETY-SYNC]"

MSG_NO_FIRST_REPORT = (
    f"{TAG} Action non transférée dans Smartsheet : elle n'est pas liée à une réponse "
    "« Premier Signalement » (ou « En Panne » / « En Service mais Maintenance » / « Non OK » "
    "pour les jeux électroniques). Pour qu'un problème remonte dans le Smartsheet Travaux, "
    "créez l'action depuis la réponse « Premier Signalement », avec photo et commentaire. "
    "Pour « Déjà signalé » / « Signalé dans l'Hebdo », liez l'action existante au lieu "
    "d'en créer une nouvelle."
)
MSG_NO_SITE = f"{TAG} Action non transférée : aucun site (parc) n'est renseigné sur l'action."
MSG_NO_SHEET = (
    f"{TAG} Action non transférée : aucune feuille Smartsheet « Travaux … ({{site}}) » "
    "n'a été trouvée pour ce parc. Contactez l'administrateur."
)
MSG_NO_SECTION = (
    f"{TAG} Action non transférée : la feuille « {{sheet}} » n'a pas de ligne "
    "« {section} ». Contactez l'administrateur."
)
MSG_TRANSFERRED = (
    f"{TAG} Transférée le {{when}} dans la feuille Smartsheet « {{sheet}} » "
    "(section {section}). Suivi dans Smartsheet : l'action passera en « Complete » "
    "quand la case « Statut Mgr » sera cochée."
)


@dataclass
class Outcome:
    kind: str  # transferee | deja_presente | rejetee | erreur
    unique_id: str
    site: str
    detail: str = ""


@dataclass
class TransferReport:
    outcomes: list[Outcome] = field(default_factory=list)
    dry_run: bool = False

    def add(self, outcome: Outcome) -> None:
        self.outcomes.append(outcome)
        log = logger.error if outcome.kind == "erreur" else logger.info
        log(
            "%s%s %s (%s) : %s",
            "[DRY-RUN] " if self.dry_run else "",
            outcome.unique_id,
            outcome.kind,
            outcome.site or "?",
            outcome.detail,
        )

    @property
    def counts(self) -> Counter:
        return Counter(o.kind for o in self.outcomes)

    @property
    def errors(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.kind == "erreur"]


class SheetCache:
    """Feuilles Travaux lues une seule fois par exécution."""

    def __init__(self, ss: SmartsheetAPI, settings: SyncSettings) -> None:
        self.ss = ss
        self.settings = settings
        self.by_site, duplicates = find_travaux_sheets(ss.list_sheets(), settings.sheet_pattern)
        for dup in duplicates:
            logger.warning("Smartsheet : feuille Travaux en double pour %s", dup)
        self.duplicates = duplicates
        self._loaded: dict[int, TravauxSheet] = {}
        logger.info("Smartsheet : %s feuille(s) Travaux", len(self.by_site))

    def for_site(self, site: str) -> TravauxSheet | None:
        found = self.by_site.get(site)
        if not found:
            return None
        sheet_id, _ = found
        if sheet_id not in self._loaded:
            self._loaded[sheet_id] = TravauxSheet.from_api(
                self.ss.get_sheet(sheet_id), self.settings
            )
        return self._loaded[sheet_id]


def run_transfer(
    mitti: MittiAPI,
    ss: SmartsheetAPI,
    settings: SyncSettings,
    mitti_settings: MittiSettings,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
    sheets: SheetCache | None = None,
) -> TransferReport:
    now = now or datetime.now(settings.tz)
    report = TransferReport(dry_run=dry_run)
    sheets = sheets or SheetCache(ss, settings)
    since = now - timedelta(days=settings.lookback_days)
    templates = set(settings.template_ids)

    for summary in mitti.iter_actions(status_ids=[STATUS_TODO], created_from=since):
        uid = summary.get("unique_id") or summary["task_id"]
        if templates and summary.get("template_id") not in templates:
            continue
        try:
            _process(mitti, sheets, settings, mitti_settings, summary, report, dry_run, now)
        except Exception as exc:  # une action en erreur ne bloque pas les autres
            report.add(Outcome("erreur", uid, task_site(summary), str(exc)))
    logger.info("Transfert terminé : %s", dict(report.counts))
    return report


def _process(
    mitti: MittiAPI,
    sheets: SheetCache,
    settings: SyncSettings,
    mitti_settings: MittiSettings,
    summary: dict,
    report: TransferReport,
    dry_run: bool,
    now: datetime,
) -> None:
    task_id = summary["task_id"]
    task = mitti.get_action(task_id) or summary
    uid = (task.get("unique_id") or summary.get("unique_id") or task_id).upper()
    site = task_site(task) or task_site(summary)

    def reject(message: str, detail: str) -> None:
        if not dry_run:
            mitti.set_status(task_id, STATUS_CANT_DO)
            mitti.add_comment(task_id, message)
            time.sleep(settings.write_pause_seconds)
        report.add(Outcome("rejetee", uid, site, detail))

    ref = find_valid_reference(task, settings.valid_labels)
    if ref is None:
        return reject(MSG_NO_FIRST_REPORT, "pas de Premier Signalement")
    if not site:
        return reject(MSG_NO_SITE, "pas de site")
    sheet = sheets.for_site(site)
    if sheet is None:
        return reject(MSG_NO_SHEET.format(site=site), "pas de feuille Travaux")
    if sheet.section_row_id is None:
        return reject(
            MSG_NO_SECTION.format(sheet=sheet.name, section=settings.section_title),
            f"pas de section {settings.section_title} dans « {sheet.name} »",
        )

    when = now.astimezone(settings.tz).strftime("%d/%m/%Y")
    follow_up = MSG_TRANSFERRED.format(when=when, sheet=sheet.name, section=settings.section_title)

    if uid in sheet.by_unique_id:
        # Ligne déjà créée (ex. mise à jour Mitti échouée la nuit précédente) : pas de doublon.
        if not dry_run:
            mitti.set_status(task_id, STATUS_IN_PROGRESS)
            mitti.add_comment(task_id, follow_up)
        report.add(Outcome("deja_presente", uid, site, sheet.name))
        return

    ctx = ref.get("inspection_context") or {}
    row = build_row(task, ctx, sheet, settings, mitti_settings, site, uid)
    media = (ctx.get("inspection_item_attachments") or {}).get("media") or []

    if dry_run:
        report.add(Outcome("transferee", uid, site, f"{sheet.name} ({len(media)} photo(s))"))
        return

    row_id = sheets.ss.add_rows(sheet.id, [row])[0]["id"]
    # Mémorise la ligne pour l'anti-doublon dans la suite de l'exécution.
    sheet.by_unique_id[uid] = SheetRow(id=row_id, values={})
    attached = _attach_media(mitti, sheets.ss, sheet.id, row_id, uid, media)
    mitti.set_status(task_id, STATUS_IN_PROGRESS)
    mitti.add_comment(task_id, follow_up)
    time.sleep(settings.write_pause_seconds)
    report.add(Outcome("transferee", uid, site, f"{sheet.name} ({attached}/{len(media)} photo(s))"))


def build_row(
    task: dict,
    ctx: dict,
    sheet: TravauxSheet,
    settings: SyncSettings,
    mitti_settings: MittiSettings,
    site: str,
    uid: str,
) -> dict[str, Any]:
    cols = sheet.columns
    question = (
        ctx.get("inspection_item_name")
        or (task.get("inspection_item") or {}).get("inspection_item_name")
        or "Sans question"
    )
    note = ((ctx.get("inspection_item_attachments") or {}).get("note") or "").strip()
    reporter = task_creator(task)
    inspection_id = ctx.get("inspection_id") or (task.get("inspection") or {}).get("inspection_id")
    link = (
        mitti_settings.inspection_url.format(inspection_id=inspection_id) if inspection_id else ""
    )
    created = parse_dt(task.get("created_at"))
    start = created.astimezone(settings.tz).date().isoformat() if created else None

    comment_parts = [note or "/"]
    if reporter and not cols["reporter"]:
        comment_parts.append(f"Signalé par : {reporter}")
    if link and not cols["report_link"]:
        comment_parts.append(f"Rapport Mitti : {link}")

    cells: list[dict[str, Any]] = []

    def put(key: str, value: Any, **extra: Any) -> None:
        if cols.get(key) and value not in (None, ""):
            cells.append({"columnId": cols[key], "value": value, **extra})

    put("site", site)
    put("name", task.get("title") or "Sans titre")
    put("details", f"[{uid}] ~ {question}")
    put("comment_mgr", "\n".join(comment_parts))
    put("start", start)
    put("reporter", reporter)
    if link:
        put("report_link", "Rapport", hyperlink={"url": link})
    if not cells:
        raise ValueError(f"aucune colonne reconnue dans « {sheet.name} »")
    return {"siblingId": sheet.section_row_id, "cells": cells}


def _attach_media(
    mitti: MittiAPI, ss: SmartsheetAPI, sheet_id: int, row_id: int, uid: str, media: list[dict]
) -> int:
    attached = 0
    for index, item in enumerate(media, start=1):
        ext = _extension(item)
        filename = f"{uid}-{index}.{ext}"
        try:
            url = mitti.media_url(item, filename)
            content, content_type = mitti.download(url)
            ss.attach_file(sheet_id, row_id, filename, content, content_type)
            attached += 1
        except Exception as exc:
            logger.error("%s : photo %s non jointe : %s", uid, filename, exc)
    return attached


def _extension(media: dict) -> str:
    name = media.get("filename") or ""
    if "." in name:
        return name.rsplit(".", 1)[1].lower()[:4]
    return {"MEDIA_TYPE_VIDEO": "mp4", "MEDIA_TYPE_PDF": "pdf"}.get(
        media.get("media_type") or "", "jpg"
    )
