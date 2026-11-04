from datetime import UTC, datetime

import httpx
import pytest
from conftest import MITTI, SS, action, body, reference, row, sheet_payload

from safetysync.completion import run_completion
from safetysync.mitti import (
    STATUS_CANT_DO,
    STATUS_COMPLETE,
    STATUS_IN_PROGRESS,
    STATUS_TODO,
)
from safetysync.transfer import SheetCache, run_transfer

NOW = datetime(2026, 11, 4, 5, 0, tzinfo=UTC)
MEDIA = [{"id": "m1", "token": "tk", "filename": "photo.jpeg", "media_type": "MEDIA_TYPE_IMAGE"}]


@pytest.fixture
def scenario(router):
    router.get(f"{SS}/sheets").respond(
        json={
            "data": [
                {"id": 100, "name": "Travaux GP Le Mans (GP-LMA)"},
                {"id": 200, "name": "Travaux GP Sans Section (GP-SEC)"},
            ]
        }
    )
    router.get(f"{SS}/sheets/100").respond(
        json=sheet_payload(
            100,
            "Travaux GP Le Mans (GP-LMA)",
            [
                row(400, name="TRAVAUX : INTERNE"),
                row(500, name="MAINTENANCE JEUX"),
                row(501, site="GP-LMA", name="Déjà là", details="[AC-9] ~ Question"),
            ],
        )
    )
    router.get(f"{SS}/sheets/200").respond(
        json=sheet_payload(200, "Travaux GP Sans Section (GP-SEC)", [row(1, name="AUTRE")])
    )
    tasks = {
        "t1": action(
            "t1",
            "AC-1",
            "GP-LMA",
            STATUS_TODO,
            creator={"firstname": "Léa", "lastname": "Martin"},
            references=[reference("Premier Signalement", "Filet troué", MEDIA)],
        ),
        "t2": action(
            "t2", "AC-2", "GP-LMA", STATUS_TODO, references=[reference("Signalé dans l'Hebdo")]
        ),
        "t3": action(
            "t3", "AC-3", "GP-XXX", STATUS_TODO, references=[reference("Premier Signalement")]
        ),
        "t4": action("t4", "AC-4", "GP-SEC", STATUS_TODO, references=[reference("En Panne")]),
        "t9": action(
            "t9", "AC-9", "GP-LMA", STATUS_TODO, references=[reference("Premier Signalement")]
        ),
    }
    router.post(f"{MITTI}/tasks/v1/actions/list").respond(
        json={
            "actions": [
                {"task": {k: v for k, v in t.items() if k != "references"}} for t in tasks.values()
            ]
        }
    )
    for tid, task in tasks.items():
        router.get(f"{MITTI}/tasks/v1/actions/{tid}").respond(json={"action": {"task": task}})
    routes = {
        "status": router.put(url__regex=rf"{MITTI}/tasks/v1/actions/\w+/status").respond(json={}),
        "comment": router.post(f"{MITTI}/tasks/v1/timeline/comments").respond(json={}),
        "media": router.get(f"{MITTI}/media/v1/download/m1").respond(
            json={"url": "https://s3.test/photo"}
        ),
        "s3": router.get("https://s3.test/photo").respond(
            content=b"JPEG", headers={"content-type": "image/jpeg"}
        ),
        "add": router.post(f"{SS}/sheets/100/rows").respond(json={"result": [{"id": 777}]}),
        "attach": router.post(f"{SS}/sheets/100/rows/777/attachments").respond(json={}),
    }
    return routes


def statuses(route):
    return {c.request.url.path.split("/")[-2]: body(c)["status_id"] for c in route.calls}


def test_transfert(mitti, ss, sync_settings, mitti_settings, scenario):
    report = run_transfer(mitti, ss, sync_settings, mitti_settings, now=NOW)

    kinds = {o.unique_id: o.kind for o in report.outcomes}
    assert kinds == {
        "AC-1": "transferee",
        "AC-2": "rejetee",
        "AC-3": "rejetee",
        "AC-4": "rejetee",
        "AC-9": "deja_presente",
    }

    # Une seule ligne ajoutée, juste sous MAINTENANCE JEUX
    assert scenario["add"].call_count == 1
    new_row = body(scenario["add"].calls[0])[0]
    assert new_row["siblingId"] == 500
    cells = {c["columnId"]: c["value"] for c in new_row["cells"]}
    assert cells[1] == "GP-LMA"
    assert cells[2] == "Titre AC-1"
    assert cells[4] == "[AC-1] ~ Propreté (Grande Struct.)"
    assert cells[7] == "2026-11-01"
    assert cells[10] == (
        "Filet troué\nSignalé par : Léa Martin\nRapport Mitti : https://app.test/inspection/audit_1"
    )

    # Photo jointe à la ligne
    attach = scenario["attach"].calls[0].request
    assert attach.content == b"JPEG"
    assert 'filename="AC-1-1.jpeg"' in attach.headers["Content-Disposition"]

    assert statuses(scenario["status"]) == {
        "t1": STATUS_IN_PROGRESS,
        "t9": STATUS_IN_PROGRESS,
        "t2": STATUS_CANT_DO,
        "t3": STATUS_CANT_DO,
        "t4": STATUS_CANT_DO,
    }
    comments = {body(c)["task_id"]: body(c)["comment"] for c in scenario["comment"].calls}
    assert "Transférée le 04/11/2026" in comments["t1"]
    assert "Premier Signalement" in comments["t2"]
    assert "(GP-XXX)" in comments["t3"]
    assert "MAINTENANCE JEUX" in comments["t4"]


def test_transfert_dry_run_n_ecrit_rien(mitti, ss, sync_settings, mitti_settings, scenario):
    report = run_transfer(mitti, ss, sync_settings, mitti_settings, now=NOW, dry_run=True)
    assert report.counts["transferee"] == 1
    for name in ("status", "comment", "add", "attach"):
        assert scenario[name].call_count == 0


def test_transfert_erreur_isolee(mitti, ss, sync_settings, mitti_settings, scenario, router):
    router.get(f"{MITTI}/tasks/v1/actions/t2").respond(500)
    report = run_transfer(mitti, ss, sync_settings, mitti_settings, now=NOW)
    assert [o.unique_id for o in report.errors] == ["AC-2"]
    assert report.counts["transferee"] == 1


def test_synchro_terminees(mitti, ss, sync_settings, router):
    router.get(f"{SS}/sheets").respond(
        json={"data": [{"id": 100, "name": "Travaux GP Le Mans (GP-LMA)"}]}
    )
    router.get(f"{SS}/sheets/100").respond(
        json=sheet_payload(
            100,
            "Travaux (GP-LMA)",
            [
                row(1, details="[AC-1] ~ x", mgr=True),  # coché → Complete dans Mitti
                row(2, details="[AC-2] ~ x", comment="Vu"),  # Complete dans Mitti → coché
                row(3, details="[AC-3] ~ x", mgr=True),  # déjà Complete partout
                row(4, details="[AC-4] ~ x", mgr=True),  # Can't do : jamais touché
                row(5, details="[AC-5] ~ x"),  # en cours partout
            ],
        )
    )
    router.post(f"{MITTI}/tasks/v1/actions/list").respond(
        json={
            "actions": [
                {"task": action("t1", "AC-1", "GP-LMA", STATUS_IN_PROGRESS)},
                {
                    "task": action(
                        "t2", "AC-2", "GP-LMA", STATUS_COMPLETE, completed_at="2026-11-02T15:00:00Z"
                    )
                },
                {"task": action("t3", "AC-3", "GP-LMA", STATUS_COMPLETE)},
                {"task": action("t4", "AC-4", "GP-LMA", STATUS_CANT_DO)},
                {"task": action("t5", "AC-5", "GP-LMA", STATUS_IN_PROGRESS)},
            ]
        }
    )
    status = router.put(url__regex=rf"{MITTI}/tasks/v1/actions/\w+/status").respond(json={})
    comment = router.post(f"{MITTI}/tasks/v1/timeline/comments").respond(json={})
    update = router.put(f"{SS}/sheets/100/rows").respond(json={"result": []})

    report = run_completion(mitti, SheetCache(ss, sync_settings), sync_settings, now=NOW)

    assert report.counts["mitti_complete"] == 1
    assert report.counts["smartsheet_coche"] == 1
    assert statuses(status) == {"t1": STATUS_COMPLETE}
    assert "Statut Mgr" in body(comment.calls[0])["comment"]
    rows = body(update.calls[0])
    assert rows[0]["id"] == 2
    cells = {c["columnId"]: c["value"] for c in rows[0]["cells"]}
    assert cells[9] is True
    assert cells[8] == "2026-11-02"
    assert cells[10].startswith("Vu\n[Action passée en Complete sur Mitti")


def test_ligne_ss_non_ajoutee_si_echec(mitti, ss, sync_settings, mitti_settings, scenario, router):
    router.post(f"{SS}/sheets/100/rows").mock(return_value=httpx.Response(400, json={}))
    report = run_transfer(mitti, ss, sync_settings, mitti_settings, now=NOW)
    # L'action reste « To do » : elle sera retentée la nuit suivante
    assert [o.unique_id for o in report.errors] == ["AC-1"]
    assert "t1" not in statuses(scenario["status"])
