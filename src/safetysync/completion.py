"""Étape 3 : synchronisation des actions terminées entre Smartsheet et Mitti.

- SYNC-10 : « Statut Mgr » coché dans Smartsheet et action Mitti « To do » / « In progress »
  → l'action passe « Complete » avec un commentaire.
- SYNC-11 : action Mitti « Complete » et « Statut Mgr » non coché → la case est cochée,
  « Fin » reçoit la date de clôture Mitti (si vide) et le Commentaire Manager une mention.
- SYNC-12 : pas de réouverture : une case décochée ou une action rouverte n'est pas
  répercutée. Les actions « Can't do » ne sont jamais modifiées.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from safetysync.config import SyncSettings
from safetysync.mitti import (
    STATUS_COMPLETE,
    STATUS_IN_PROGRESS,
    STATUS_TODO,
    MittiAPI,
    parse_dt,
    task_status_id,
)
from safetysync.transfer import TAG, SheetCache

logger = logging.getLogger(__name__)

MSG_DONE_IN_SMARTSHEET = (
    f"{TAG} Action passée en « Complete » : la case « Statut Mgr » a été cochée dans "
    "Smartsheet (constaté le {when})."
)
SS_DONE_IN_MITTI = "[Action passée en Complete sur Mitti, case cochée auto le {when}]"


@dataclass
class CompletionReport:
    counts: Counter = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False


def run_completion(
    mitti: MittiAPI,
    sheets: SheetCache,
    settings: SyncSettings,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
    index: dict[str, dict] | None = None,
) -> CompletionReport:
    now = now or datetime.now(settings.tz)
    report = CompletionReport(dry_run=dry_run)
    when = now.astimezone(settings.tz).strftime("%d/%m/%Y")
    if index is None:
        index = index_actions(mitti, now - timedelta(days=settings.completion_horizon_days))
    prefix = "[DRY-RUN] " if dry_run else ""

    for site in sorted(sheets.by_site):
        try:
            sheet = sheets.for_site(site)
        except Exception as exc:
            report.errors.append(f"lecture feuille {site} : {exc}")
            logger.error("Smartsheet : lecture de la feuille %s impossible : %s", site, exc)
            continue
        if sheet is None:
            continue
        if not sheet.columns["details"] or not sheet.columns["status_mgr"]:
            report.errors.append(f"« {sheet.name} » : colonnes Détails / Statut Mgr introuvables")
            continue
        for uid, row in sheet.ac_rows():
            report.counts["lignes"] += 1
            task = index.get(uid)
            if not task:
                report.counts["inconnues_mitti"] += 1
                continue
            status = task_status_id(task)
            checked = sheet.is_done(row)
            try:
                if checked and status in (STATUS_TODO, STATUS_IN_PROGRESS):
                    logger.info("%s%s : coché dans Smartsheet → Complete dans Mitti", prefix, uid)
                    if not dry_run:
                        mitti.set_status(task["task_id"], STATUS_COMPLETE)
                        mitti.add_comment(task["task_id"], MSG_DONE_IN_SMARTSHEET.format(when=when))
                        time.sleep(settings.write_pause_seconds)
                    task.setdefault("status", {})["status_id"] = STATUS_COMPLETE
                    report.counts["mitti_complete"] += 1
                elif not checked and status == STATUS_COMPLETE:
                    logger.info(
                        "%s%s : Complete dans Mitti → case cochée dans Smartsheet", prefix, uid
                    )
                    if not dry_run:
                        update = _done_row(sheet, row, task, when, settings)
                        sheets.ss.update_rows(sheet.id, [update])
                        time.sleep(settings.write_pause_seconds)
                    row.values[sheet.columns["status_mgr"]] = True
                    report.counts["smartsheet_coche"] += 1
            except Exception as exc:
                report.errors.append(f"{uid} : {exc}")
                logger.error("%s : synchronisation impossible : %s", uid, exc)
    logger.info("Synchro des terminées : %s", dict(report.counts))
    return report


def index_actions(mitti: MittiAPI, since: datetime) -> dict[str, dict]:
    """unique_id (AC-xxx) -> task, pour toutes les actions créées depuis `since`."""
    index = {}
    for task in mitti.iter_actions(created_from=since):
        uid = (task.get("unique_id") or "").upper()
        if uid:
            index[uid] = task
    return index


def _done_row(sheet, row, task: dict, when: str, settings: SyncSettings) -> dict:
    cols = sheet.columns
    cells = [{"columnId": cols["status_mgr"], "value": True}]
    if cols["end"] and not row.get(cols["end"]):
        done_at = parse_dt(task.get("completed_at")) or parse_dt(task.get("modified_at"))
        done_at = done_at or datetime.now(settings.tz)
        day = done_at.astimezone(settings.tz).date()
        cells.append({"columnId": cols["end"], "value": day.isoformat()})
    if cols["comment_mgr"]:
        current = str(row.get(cols["comment_mgr"]) or "").strip()
        mention = SS_DONE_IN_MITTI.format(when=when)
        cells.append({"columnId": cols["comment_mgr"], "value": f"{current}\n{mention}".strip()})
    return {"id": row.id, "cells": cells}
