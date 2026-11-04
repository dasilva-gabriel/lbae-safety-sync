from datetime import date
from zoneinfo import ZoneInfo

from conftest import MITTI, SS, action, row, sheet_payload

from safetysync.mitti import STATUS_CANT_DO, STATUS_IN_PROGRESS, STATUS_TODO
from safetysync.report.analysis import (
    BLEU,
    GRIS,
    JAUNE,
    PANNE,
    ROUGE,
    VERT,
    ActionStats,
    DayCell,
    GameState,
    Inspection,
    day_cell,
    game_name,
    game_states,
    hebdo_cell,
    opening_days,
    priority,
    week_days,
)
from safetysync.report.collect import collect_weekly, previous_monday
from safetysync.report.render import render, subject
from safetysync.transfer import SheetCache

TZ = ZoneInfo("Europe/Paris")
MONDAY = date(2026, 10, 19)


def insp(site, day, *, completed=True, duration=900, score=90.0, started=None, iid="i"):
    return Inspection(iid, site, day, started or day, completed, duration, score, "https://r")


def test_semaine_precedente():
    assert previous_monday(date(2026, 10, 26)) == MONDAY  # lundi → semaine d'avant
    assert previous_monday(date(2026, 10, 28)) == MONDAY


def test_jours_ouverts():
    days = week_days(MONDAY)
    parcs = [f"P{i}" for i in range(10)]
    journaliers = [insp(p, days[0]) for p in parcs[:3]] + [insp("P0", days[1])]
    # 3/10 parcs le lundi (ouvert), 1/10 le mardi (< 20 % : fermé)
    assert opening_days(days, journaliers, parcs, 0.2) == {days[0]}


def test_cellules_journalier():
    d = MONDAY
    assert day_cell([], True, 300).color == ROUGE
    assert day_cell([], False, 300).color == GRIS
    assert day_cell([insp("A", d)], True, 300) == DayCell(VERT, "90%", [], "https://r")
    assert day_cell([insp("A", d, completed=False)], True, 300).color == JAUNE
    short = day_cell([insp("A", d, duration=157)], True, 300)
    assert short.color == JAUNE and short.issues == ["Durée non cohérente (157 sec)"]
    two = day_cell([insp("A", d, score=71.429), insp("A", d, score=92.857)], True, 300)
    assert two.issues == ["Score non cohérent (71.4% à 92.9%)"]
    early = day_cell([insp("A", d, started=date(2026, 10, 18))], True, 300)
    assert early.color == BLEU
    late = day_cell([insp("A", d, started=date(2026, 10, 21))], True, 300)
    assert late.color == JAUNE and "a posteriori" in late.issues[0]


def test_cellule_hebdo():
    assert hebdo_cell([]).color == ROUGE
    assert hebdo_cell([insp("A", MONDAY)]).text == "1 trouvé"
    assert hebdo_cell([insp("A", MONDAY, completed=False)]).color == JAUNE


def test_nom_des_jeux():
    p = "[Jeu Elec.]"
    assert game_name("[Jeu Elec.] Vérification de tous les Kartings", p) == "Kartings"
    assert game_name("[Jeu Elec.] Les Air Hockey sont-ils tous fonctionnels ?", p) == "Air Hockey"
    assert game_name("[Jeu Elec.] Le Kid Challenge est-il fonctionnel ?", p) == "Kid Challenge"
    assert (
        game_name("12.0. [Jeu Elec.] La Néo Expérience est-elle fonctionnelle ?", p)
        == "Néo Expérience"
    )
    assert (
        game_name('[Jeu Elec.] Les "Zones Tap-Tap" (cibles) sont-elles toutes fonctionnelles ?', p)
        == '"Zones Tap-Tap" (cibles)'
    )


def test_etat_des_jeux_et_semaines_de_panne():
    hebdos = [
        insp("A", date(2026, 10, 5), iid="h1"),
        insp("A", date(2026, 10, 12), iid="h2"),
        insp("A", date(2026, 10, 20), iid="h3"),
        insp("A", date(2026, 10, 27), iid="h4"),  # après la semaine : ignoré
    ]
    kart = "[Jeu Elec.] Vérification de tous les Kartings"
    hockey = "[Jeu Elec.] Les Air Hockey sont-ils tous fonctionnels ?"
    items = {
        "h1": [{"label": kart, "response": "OK et En Service"}],
        "h2": [{"label": kart, "response": "En Panne"}],
        "h3": [
            {"label": kart, "response": "En Panne", "comment": "Batterie"},
            {"label": hockey, "response": "En Service mais Maintenance"},
            {"label": "Autre question", "response": "En Panne"},
        ],
        "h4": [{"label": kart, "response": "OK et En Service"}],
    }
    states = game_states(items, hebdos, "[Jeu Elec.]", date(2026, 10, 25))
    assert states["A"]["Kartings"] == GameState(PANNE, 2, "Batterie")
    assert states["A"]["Air Hockey"].state == "maintenance"
    assert "Autre question" not in states["A"]


def test_priorite():
    cells = [DayCell(ROUGE, "")] * 2 + [DayCell(VERT, "")] * 5
    p = priority(
        "A",
        cells,
        DayCell(ROUGE, ""),
        {"Kartings": GameState(PANNE, 3)},
        ActionStats(a_traiter=4, anciennes=2),
    )
    # panne 5+2, hebdo 4, 2 journaliers 4, actions 2+1
    assert p.score == 18
    assert p.reasons[0] == "1 jeu(x) en panne (Kartings depuis 3 sem.)"
    calm = priority("B", [DayCell(VERT, "")] * 7, DayCell(VERT, ""), {}, ActionStats())
    assert calm.score == 0 and calm.reasons == []


def test_collecte_et_rendu(mitti, ss, router, report_settings, sync_settings):
    report_settings.parcs_file.write_text(
        '[[regions]]\nnom = "Carine (Nord)"\nresponsable = "Carine"\nparcs = ["GP-A", "GP-B"]\n',
        encoding="utf-8",
    )
    router.get(f"{MITTI}/feed/sites").respond(
        json={
            "data": [
                {"id": "s1", "name": "GP-A"},
                {"id": "s2", "name": "GP-B"},
                {"id": "s3", "name": "GP-C"},
            ]
        }
    )

    def journalier(i, site, day, **kw):
        return {
            "id": f"j{i}",
            "site_id": site,
            "conducted_on": f"{day}T08:00:00Z",
            "date_started": f"{day}T08:00:00Z",
            "date_completed": f"{day}T08:20:00Z",
            "duration": 1200,
            "score_percentage": 95.0,
            "web_report_link": "https://r",
            **kw,
        }

    router.get(f"{MITTI}/feed/inspections", params={"template": "tpl_j"}).respond(
        json={
            "data": [
                journalier(1, "s1", "2026-10-19"),
                journalier(2, "s2", "2026-10-19", duration=120),
                journalier(3, "s3", "2026-10-20"),
                journalier(4, "s1", "2026-10-12"),  # semaine précédente : ignoré
            ]
        }
    )
    router.get(f"{MITTI}/feed/inspections", params={"template": "tpl_h"}).respond(
        json={
            "data": [
                journalier(9, "s1", "2026-10-21") | {"id": "h1"},
            ]
        }
    )
    router.get(f"{MITTI}/feed/inspection_items").respond(
        json={
            "data": [
                {
                    "audit_id": "h1",
                    "label": "[Jeu Elec.] Les Flippers sont-ils tous fonctionnels ?",
                    "response": "En Panne",
                },
                {"audit_id": "h1", "label": "Propreté", "response": "Vérifié et OK"},
            ]
        }
    )
    router.post(f"{MITTI}/tasks/v1/actions/list").respond(
        json={
            "actions": [
                {"task": action("t1", "AC-1", "GP-A", STATUS_IN_PROGRESS, "2026-10-20T10:00:00Z")},
                {"task": action("t2", "AC-2", "GP-A", STATUS_CANT_DO, "2026-10-21T10:00:00Z")},
                {"task": action("t3", "AC-3", "GP-B", STATUS_TODO, "2026-10-22T10:00:00Z")},
            ]
        }
    )
    router.get(f"{SS}/sheets").respond(json={"data": [{"id": 1, "name": "Travaux A (GP-A)"}]})
    router.get(f"{SS}/sheets/1").respond(
        json=sheet_payload(
            1,
            "Travaux A (GP-A)",
            [
                row(10, details="[AC-1] ~ q", start="2026-10-20"),
                row(11, details="[AC-0] ~ q", start="2026-08-01"),
                row(12, details="[AC-7] ~ q", mgr=True, end="2026-10-22"),
            ],
        )
    )

    rep = collect_weekly(
        mitti, SheetCache(ss, sync_settings), report_settings, sync_settings, MONDAY
    )

    assert [r.nom for r in rep.referentiel.regions] == [
        "Carine (Nord)",
        "Autres parcs (non référencés)",
    ]
    assert rep.referentiel.regions[1].parcs == ["GP-C"]
    # Lundi : 2/3 parcs ; mardi : 1/3 → ouverts ; mercredi… : fermés
    assert rep.open_days == {date(2026, 10, 19), date(2026, 10, 20)}
    assert [c.color for c in rep.day_cells["GP-A"][:3]] == [VERT, ROUGE, GRIS]
    assert rep.day_cells["GP-B"][0].color == JAUNE
    assert rep.hebdo["GP-A"].color == VERT and rep.hebdo["GP-B"].color == ROUGE
    assert rep.games["GP-A"]["Flippers"].state == PANNE
    a = rep.stats["GP-A"]
    assert (a.creees, a.transferees, a.rejetees) == (2, 1, 1)
    assert (a.a_traiter, a.anciennes, a.traitees_semaine) == (2, 1, 1)
    assert a.plus_ancienne == date(2026, 8, 1)
    assert rep.stats["GP-B"].ouvertes == 1
    assert rep.priorities[0].site == "GP-A"

    html = render(rep)
    assert subject(rep) == "Rapport maintenance du réseau du 19/10/2026 au 25/10/2026"
    assert "Région : Carine (Nord) — Carine" in html
    assert "Durée non cohérente (120 sec)" in html
    assert "Flippers" in html and "Parcs prioritaires" in html


def test_priorite_plafond_actions():
    p = priority(
        "A",
        [DayCell(VERT, "")] * 7,
        DayCell(VERT, ""),
        {},
        ActionStats(a_traiter=167, anciennes=157),
    )
    assert p.score == 8


def test_trames_non_configurees(mitti, ss, router, report_settings, sync_settings):
    report_settings.journalier_template_ids = []
    report_settings.hebdo_template_ids = []
    router.get(f"{MITTI}/feed/sites").respond(json={"data": [{"id": "s1", "name": "GP-A"}]})
    router.post(f"{MITTI}/tasks/v1/actions/list").respond(
        json={
            "actions": [
                {"task": action("t1", "AC-1", "GP-A", STATUS_TODO, "2026-10-20T10:00:00Z")},
            ]
        }
    )
    router.get(f"{SS}/sheets").respond(json={"data": []})
    rep = collect_weekly(
        mitti, SheetCache(ss, sync_settings), report_settings, sync_settings, MONDAY
    )
    assert rep.hebdo["GP-A"].text == "Non configuré"
    assert {c.color for c in rep.day_cells["GP-A"]} == {GRIS}
    assert "hebdo manquant" not in rep.priorities[0].reasons
