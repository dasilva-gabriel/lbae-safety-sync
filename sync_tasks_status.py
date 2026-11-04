import os
import re
import time
from datetime import datetime, date
from typing import Dict, Optional, List, Tuple

import requests
import smartsheet

from comp.logger import logger
from comp.globals import (
    SAFETYCULTURE_API_TOKEN,
    SMARTSHEET_API_TOKEN,
    TERMINATED_ID,
)

# =========================
# CONFIG Smartsheet (titres de colonnes)
# =========================
COLUMN_DETAILS = "Détails"                 # contient "[AC-8934] ~ ..."
COLUMN_STATUS_MGR = "Statut Mgr"           # checkbox
COLUMN_COMMENT_MGR = "Commentaire Manager" # texte (append)
COLUMN_END = "Fin"                         # date

SS_APPEND_MSG = "[Action passée en Terminé sur Safety, case cochée auto]"
SC_APPEND_MSG_TEMPLATE = "[SAFETYLINK] Automatique : action détectée comme cochée sur Smartsheet le {dt}."

RE_UNIQUE_ID = re.compile(r"\[(AC-[^\]]+)\]")
# >>> Le point clé: on filtre les sheets AVANT de les charger
RE_SHEET_TRAVAUX = re.compile(r"(?i)\btravaux\b.*\((?P<site>[^)]+)\)")

SLEEP_BETWEEN_UPDATES_SEC = 0.2


# ======================================================================================
# SafetyCulture client
# ======================================================================================
class SafetyCultureClient:
    def __init__(self, token: str):
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "accept": "application/json",
        }
        self._details_cache: Dict[str, Dict] = {}

    def list_all_actions_index(self, page_size: int = 100) -> Dict[str, Dict]:
        """
        Retour: unique_id -> {task_id, status_id, site}
        """
        url = "https://api.safetyculture.io/tasks/v1/actions/list"
        page_token = None
        out: Dict[str, Dict] = {}

        logger.info("SafetyCulture: récupération actions (pagination).")
        while True:
            payload = {"page_size": page_size}
            if page_token:
                payload["page_token"] = page_token

            try:
                r = requests.post(url, headers=self.headers, json=payload, timeout=60)
                r.raise_for_status()
                data = r.json()
            except requests.RequestException as e:
                logger.error(f"SafetyCulture: erreur list actions: {e}")
                break

            actions = data.get("actions", []) or []
            for a in actions:
                task = (a or {}).get("task", {}) or {}
                unique_id = task.get("unique_id")
                task_id = task.get("task_id")
                status_id = task.get("status_id") or (task.get("status", {}) or {}).get("status_id")
                site = (task.get("site", {}) or {}).get("name")

                if not unique_id or not task_id:
                    continue

                out[unique_id] = {
                    "task_id": task_id,
                    "status_id": status_id,
                    "site": (site or "").strip().upper(),
                }

            page_token = data.get("next_page_token")
            if not page_token:
                break

        logger.info(f"SafetyCulture: {len(out)} actions indexées par unique_id.")
        return out

    def get_action_details(self, task_id: str) -> Optional[Dict]:
        if task_id in self._details_cache:
            return self._details_cache[task_id]

        url = f"https://api.safetyculture.io/tasks/v1/actions/{task_id}"
        try:
            r = requests.get(url, headers=self.headers, timeout=60)
            r.raise_for_status()
            data = r.json()
            self._details_cache[task_id] = data
            return data
        except requests.RequestException as e:
            logger.error(f"SafetyCulture: erreur get details {task_id}: {e}")
            return None

    def update_action_status(self, task_id: str, status_id: str) -> bool:
        url = f"https://api.safetyculture.io/tasks/v1/actions/{task_id}/status"
        payload = {"status_id": status_id}
        try:
            r = requests.put(url, headers=self.headers, json=payload, timeout=60)
            r.raise_for_status()
            logger.info(f"SafetyCulture: status updated task_id={task_id} -> {status_id}")
            return True
        except requests.RequestException as e:
            logger.error(f"SafetyCulture: erreur update status task_id={task_id}: {e}")
            return False

    def update_action_description(self, task_id: str, description: str) -> bool:
        url = f"https://api.safetyculture.io/tasks/v1/actions/{task_id}/description"
        payload = {"description": description}
        try:
            r = requests.put(url, headers=self.headers, json=payload, timeout=60)
            r.raise_for_status()
            logger.info(f"SafetyCulture: description updated task_id={task_id}")
            return True
        except requests.RequestException as e:
            logger.error(f"SafetyCulture: erreur update description task_id={task_id}: {e}")
            return False

    def mark_terminated_with_note(self, unique_id: str, task_id: str, dry_run: bool = False) -> bool:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        note = SC_APPEND_MSG_TEMPLATE.format(dt=now_str)

        details = self.get_action_details(task_id) or {}
        task = (details.get("action", {}) or {}).get("task") or details.get("task") or {}
        current_desc = task.get("description") or ""

        # évite de re-append
        if note in current_desc:
            if dry_run:
                logger.info(f"[DRY-RUN] SafetyCulture: {unique_id} déjà noté, status->TERMINATED si besoin.")
                return True
            ok1 = self.update_action_status(task_id, TERMINATED_ID)
            return ok1

        updated_desc = (current_desc.strip() + "\n\n" + note).strip() if current_desc else note

        if dry_run:
            logger.info(f"[DRY-RUN] SafetyCulture: passer {unique_id} en TERMINATED + update description.")
            return True

        ok1 = self.update_action_status(task_id, TERMINATED_ID)
        time.sleep(SLEEP_BETWEEN_UPDATES_SEC)
        ok2 = self.update_action_description(task_id, updated_desc)
        return ok1 and ok2


# ======================================================================================
# Smartsheet client
# ======================================================================================
class SmartsheetClient:
    def __init__(self, token: str):
        self.client = smartsheet.Smartsheet(token)
        self.client.errors_as_exceptions(True)

    def list_sheets(self) -> List[Tuple[int, str]]:
        sheets = self.client.Sheets.list_sheets(include_all=True)
        return [(s.id, s.name) for s in sheets.data]

    def build_travaux_sheet_map(self) -> Dict[str, Tuple[int, str]]:
        """
        Map: SITE -> (sheet_id, sheet_name)
        Ne garde QUE les sheets dont le nom match "Travaux ... (SITE)"
        """
        all_sheets = self.list_sheets()
        mapping: Dict[str, Tuple[int, str]] = {}

        for sheet_id, sheet_name in all_sheets:
            m = RE_SHEET_TRAVAUX.search(sheet_name or "")
            if not m:
                continue

            site = (m.group("site") or "").strip().upper()
            if not site:
                continue

            if site in mapping:
                # on garde le 1er (comme ton script), mais on log le doublon
                logger.warning(f"Smartsheet: doublon sheet pour site {site}: '{mapping[site][1]}' et '{sheet_name}'")
                continue

            mapping[site] = (sheet_id, sheet_name)

        logger.info(f"Smartsheet: {len(mapping)} sheets Travaux détectées via regex.")
        return mapping

    def get_sheet(self, sheet_id: int):
        return self.client.Sheets.get_sheet(sheet_id)

    @staticmethod
    def find_columns(sheet) -> Dict[str, Optional[int]]:
        def norm(s: str) -> str:
            return (s or "").strip().lower()

        ids = {"details": None, "status_mgr": None, "comment_mgr": None, "end": None}

        for c in sheet.columns:
            title = norm(c.title)
            if COLUMN_DETAILS.lower() in title:
                ids["details"] = c.id
            elif COLUMN_STATUS_MGR.lower() in title:
                ids["status_mgr"] = c.id
            elif COLUMN_COMMENT_MGR.lower() in title:
                ids["comment_mgr"] = c.id
            elif COLUMN_END.lower() in title:
                ids["end"] = c.id

        return ids

    @staticmethod
    def get_cell_value(row, col_id: int):
        for cell in row.cells:
            if cell.column_id == col_id:
                return cell.value
        return None

    @staticmethod
    def extract_unique_id(details_val: str) -> Optional[str]:
        if not details_val:
            return None
        m = RE_UNIQUE_ID.search(str(details_val))
        return m.group(1).strip() if m else None

    def update_row_cells(self, sheet_id: int, row_id: int, updates: List[Tuple[int, object]], dry_run: bool = False) -> bool:
        if dry_run:
            logger.info(f"[DRY-RUN] Smartsheet: update sheet={sheet_id} row={row_id} cells={len(updates)}")
            return True

        row = smartsheet.models.Row()
        row.id = row_id
        row.cells = []

        for col_id, val in updates:
            cell = smartsheet.models.Cell()
            cell.column_id = col_id
            cell.value = val
            row.cells.append(cell)

        try:
            self.client.Sheets.update_rows(sheet_id, [row])
            logger.info(f"Smartsheet: row updated sheet={sheet_id} row={row_id}")
            return True
        except Exception as e:
            logger.error(f"Smartsheet: erreur update row sheet={sheet_id} row={row_id}: {e}")
            return False


# ======================================================================================
# Main sync (ONLY Travaux sheets)
# ======================================================================================
def sync_status(dry_run: bool = False):
    sc_token = os.getenv("SAFETYCULTURE_API_TOKEN", SAFETYCULTURE_API_TOKEN)
    ss_token = os.getenv("SMARTSHEET_API_TOKEN", SMARTSHEET_API_TOKEN)

    sc = SafetyCultureClient(sc_token)
    ss = SmartsheetClient(ss_token)

    # 1) index SafetyCulture
    actions = sc.list_all_actions_index(page_size=100)

    # 2) mapping Travaux sheets uniquement
    travaux_map = ss.build_travaux_sheet_map()

    # 3) Sites réellement présents côté SafetyCulture
    safety_sites = {info.get("site") for info in actions.values() if info.get("site")}
    selected_sheets: List[Tuple[str, int, str]] = []
    missing_sites = []

    for site in sorted(safety_sites):
        if site in travaux_map:
            sheet_id, sheet_name = travaux_map[site]
            selected_sheets.append((site, sheet_id, sheet_name))
        else:
            missing_sites.append(site)

    logger.info(f"Smartsheet: sheets Travaux ciblées: {len(selected_sheets)}")
    if missing_sites:
        logger.warning(f"Smartsheet: sites Safety sans sheet Travaux correspondante: {', '.join(missing_sites)}")

    today_str = date.today().isoformat()

    n_sc_updated = 0
    n_ss_updated = 0
    n_rows_scanned = 0
    n_uid_matched = 0

    for site, sheet_id, sheet_name in selected_sheets:
        try:
            sheet = ss.get_sheet(sheet_id)
        except Exception as e:
            logger.error(f"Smartsheet: impossible de lire '{sheet_name}' ({sheet_id}): {e}")
            continue

        col_ids = ss.find_columns(sheet)
        if not col_ids["details"] or not col_ids["status_mgr"]:
            logger.error(f"Smartsheet: sheet '{sheet_name}' ({site}) sans colonnes Détails/Statut Mgr -> ignorée.")
            continue

        logger.info(f"Smartsheet: analyse '{sheet_name}' (site={site})")

        for row in sheet.rows:
            n_rows_scanned += 1

            details_val = ss.get_cell_value(row, col_ids["details"])
            unique_id = ss.extract_unique_id(details_val)
            if not unique_id:
                continue

            action_info = actions.get(unique_id)
            if not action_info:
                # unique_id présent dans la sheet, mais pas retrouvé dans Safety
                continue

            n_uid_matched += 1
            sc_task_id = action_info["task_id"]
            sc_status_id = action_info["status_id"]

            ss_checked = bool(ss.get_cell_value(row, col_ids["status_mgr"]))

            # A) Smartsheet coché -> Safety doit être TERMINATED
            if ss_checked and sc_status_id != TERMINATED_ID:
                logger.info(f"SYNC A: {unique_id} coché Smartsheet -> TERMINATED Safety.")
                ok = sc.mark_terminated_with_note(unique_id, sc_task_id, dry_run=dry_run)
                if ok:
                    n_sc_updated += 1
                    actions[unique_id]["status_id"] = TERMINATED_ID
                time.sleep(SLEEP_BETWEEN_UPDATES_SEC)
                continue

            # B) Smartsheet non coché + Safety TERMINATED -> on coche + note + fin
            if (not ss_checked) and (sc_status_id == TERMINATED_ID):
                logger.info(f"SYNC B: {unique_id} TERMINATED Safety -> coche Smartsheet + note + fin.")
                updates = [(col_ids["status_mgr"], True)]

                if col_ids["comment_mgr"]:
                    current_comment = ss.get_cell_value(row, col_ids["comment_mgr"]) or ""
                    if SS_APPEND_MSG not in str(current_comment):
                        new_comment = (
                            (str(current_comment).strip() + "\n" + f"{SS_APPEND_MSG} ({today_str})").strip()
                            if current_comment else f"{SS_APPEND_MSG} ({today_str})"
                        )
                    else:
                        new_comment = current_comment
                    updates.append((col_ids["comment_mgr"], new_comment))

                if col_ids["end"]:
                    updates.append((col_ids["end"], today_str))

                ok = ss.update_row_cells(sheet_id, row.id, updates, dry_run=dry_run)
                if ok:
                    n_ss_updated += 1

                time.sleep(SLEEP_BETWEEN_UPDATES_SEC)
                continue

    logger.info(
        f"SYNC terminé: rows={n_rows_scanned}, uid_match={n_uid_matched}, "
        f"safety_updates={n_sc_updated}, smartsheet_updates={n_ss_updated}, dry_run={dry_run}"
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Sync Terminé SafetyCulture <-> Smartsheet (Travaux (SITE) uniquement).")
    parser.add_argument("--dry-run", action="store_true", help="Log uniquement, aucune modification.")
    args = parser.parse_args()
    sync_status(dry_run=args.dry_run)

