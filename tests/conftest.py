import json

import pytest
import respx

from safetysync.config import MittiSettings, ReportSettings, SmartsheetSettings, SyncSettings
from safetysync.mitti import MittiAPI
from safetysync.smartsheet import SmartsheetAPI

MITTI = "https://mitti.test"
SS = "https://ss.test/2.0"


def no_sleep(_):
    return None


@pytest.fixture
def mitti_settings() -> MittiSettings:
    return MittiSettings(
        api_token="tok",
        api_url=MITTI,
        inspection_url="https://app.test/inspection/{inspection_id}",
        page_size=2,
        max_retries=2,
        _env_file=None,
    )


@pytest.fixture
def sync_settings() -> SyncSettings:
    return SyncSettings(write_pause_seconds=0, _env_file=None)


@pytest.fixture
def report_settings(tmp_path) -> ReportSettings:
    return ReportSettings(
        journalier_templates="template_j",
        hebdo_templates="template_h",
        output_dir=tmp_path,
        _env_file=None,
    )


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as r:
        yield r


@pytest.fixture
def mitti(mitti_settings, router):
    with MittiAPI(mitti_settings, sleep=no_sleep) as api:
        yield api


@pytest.fixture
def ss(router):
    settings = SmartsheetSettings(api_token="tok", api_url=SS, max_retries=1, _env_file=None)
    with SmartsheetAPI(settings, sleep=no_sleep) as api:
        yield api


def body(call) -> dict:
    return json.loads(call.request.content)


# Colonnes d'une feuille Travaux type (cf. export GP-LMA)
COLUMNS = [
    {"id": 1, "title": "SITE"},
    {"id": 2, "title": "Nom de la tâche"},
    {"id": 3, "title": "! Mgr"},
    {"id": 4, "title": "Détails / Commentaires Kidea"},
    {"id": 5, "title": "Statut Kidea"},
    {"id": 7, "title": "Début"},
    {"id": 8, "title": "Fin"},
    {"id": 9, "title": "Statut Mgr"},
    {"id": 10, "title": "Commentaire Manager"},
]


def sheet_payload(sheet_id, name, rows):
    return {"id": sheet_id, "name": name, "columns": COLUMNS, "rows": rows}


def row(row_id, **cells):
    mapping = {"site": 1, "name": 2, "details": 4, "end": 8, "start": 7, "mgr": 9, "comment": 10}
    return {
        "id": row_id,
        "createdAt": "2026-10-01T10:00:00Z",
        "cells": [{"columnId": mapping[k], "value": v} for k, v in cells.items()],
    }


def action(task_id, uid, site, status_id, created="2026-11-01T10:00:00Z", **extra):
    return {
        "task_id": task_id,
        "unique_id": uid,
        "title": f"Titre {uid}",
        "site": {"name": site},
        "status": {"status_id": status_id},
        "created_at": created,
        **extra,
    }


def reference(label, note="Note", media=None, inspection_id="audit_1"):
    return {
        "inspection_context": {
            "inspection_id": inspection_id,
            "inspection_item_name": "Propreté (Grande Struct.)",
            "list_answer": {"responses": [{"label": label}]},
            "inspection_item_attachments": {"note": note, "media": media or []},
        }
    }
