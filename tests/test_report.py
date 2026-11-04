from datetime import date, datetime
from zoneinfo import ZoneInfo

from conftest import MITTI, SS, action, row, sheet_payload

from safetysync.mitti import STATUS_CANT_DO, STATUS_IN_PROGRESS, STATUS_TODO
from safetysync.parcs import referentiel_from_sites
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
    Kpi,
    Thresholds,
    breakdown_rates,
    day_cell,
    game_name,
    game_states,
    hebdo_cell,
    opening_days,
    previous_quarter_start,
    priority,
    quarter_label,
    thresholds,
    week_days,
)
from safetysync.report.collect import collect_weekly, previous_monday
from safetysync.report.render import render, subject
from safetysync.transfer import SheetCache

TZ = ZoneInfo("Europe/Paris")
MONDAY = date(2026, 10, 19)
TH = Thresholds(min_duration=300, median=900)


def insp(site, day, *, completed=True, duration=900, score=90.0, started=None, hour=9, iid="i"):
    start = datetime.combine(started or day, datetime.min.time()).replace(hour=hour, tzinfo=TZ)
    return Inspection(iid, site, day, start, completed, duration, score, "https://r")


def test_semaine_precedente():
    assert previous_monday(date(2026, 10, 26)) == MONDAY
    assert previous_monday(date(2026, 10, 28)) == MONDAY


def test_jours_ouverts():
    days = week_days(MONDAY)
    parcs = [f"P{i}" for i in range(10)]
    journaliers = [insp(p, days[0]) for p in parcs[:3]] + [insp("P0", days[1])]
    assert opening_days(days, journaliers, parcs, 0.2) == {days[0]}


def test_seuil_trop_rapide_relatif_au_reseau():
    week = [insp("A", MONDAY, duration=d) for d in (600, 900, 1200, 1500, 100)]
    th = thresholds(week, 300, 0.4)
    assert th.median == 900 and th.min_duration == 360  # 40 % de 15 min
    assert thresholds([], 300, 0.4).min_duration == 300


def test_cellules_journalier():
    d = MONDAY
    assert day_cell([], True, TH).color == ROUGE
    assert day_cell([], False, TH).color == GRIS
    assert day_cell([insp("A", d)], True, TH) == DayCell(VERT, "90 %", [], "https://r")
    assert day_cell([insp("A", d, completed=False)], True, TH).color == JAUNE
    short = day_cell([insp("A", d, duration=157)], True, TH)
    assert short.color == JAUNE and short.issues == ["trop rapide 3 min (méd. 15 min)"]
    two = day_cell([insp("A", d, score=71.429), insp("A", d, score=92.857)], True, TH)
    assert two.issues == ["scores 71.4 %→92.9 %"]
    assert day_cell([insp("A", d, started=date(2026, 10, 18))], True, TH).color == BLEU
    late = day_cell([insp("A", d, started=date(2026, 10, 21))], True, TH)
    assert late.issues == ["saisi le 21/10"]
    night = day_cell([insp("A", d, hour=2)], True, TH)
    assert night.issues == ["fait à 02h00"]
    perfect = day_cell([insp("A", d, score=100.0)], True, TH, open_issues=3)
    assert perfect.issues == ["100 % malgré 3 pb ouverts"]
    assert day_cell([insp("A", d, score=100.0)], True, TH).color == VERT


def test_cellule_hebdo():
    assert hebdo_cell([]).color == ROUGE
    assert hebdo_cell([insp("A", MONDAY)]).color == VERT
    assert hebdo_cell([insp("A", MONDAY, completed=False)]).color == JAUNE
    fast = hebdo_cell([insp("A", MONDAY, duration=200)], Thresholds(900, 2400))
    assert fast.color == JAUNE and fast.issues[0].startswith("trop rapide")


def test_nom_des_jeux():
    p = "[Jeu Elec.]"
    assert game_name("[Jeu Elec.] Vérification de tous les Kartings", p) == "Kartings"
    assert game_name("[Jeu Elec.] Les Air Hockey sont-ils tous fonctionnels ?", p) == "Air Hockey"
    assert game_name("[Jeu Elec.] Le Kid Challenge est-il fonctionnel ?", p) == "Kid Challenge"
    assert (
        game_name("12.0. [Jeu Elec.] La Néo Expérience est-elle fonctionnelle ?", p)
        == "Néo Expérience"
    )


KART = "[Jeu Elec.] Vérification de tous les Kartings"
HOCKEY = "[Jeu Elec.] Les Air Hockey sont-ils tous fonctionnels ?"


def test_etat_des_jeux_et_semaines_de_panne():
    hebdos = [
        insp("A", date(2026, 10, 5), iid="h1"),
        insp("A", date(2026, 10, 12), iid="h2"),
        insp("A", date(2026, 10, 20), iid="h3"),
        insp("A", date(2026, 10, 27), iid="h4"),
    ]
    items = {
        "h1": [{"label": KART, "response": "OK et En Service"}],
        "h2": [{"label": KART, "response": "En Panne"}],
        "h3": [
            {"label": KART, "response": "En Panne", "comment": "Batterie"},
            {"label": HOCKEY, "response": "En Service mais Maintenance"},
            {"label": "Autre question", "response": "En Panne"},
        ],
        "h4": [{"label": KART, "response": "OK et En Service"}],
    }
    states = game_states(items, hebdos, "[Jeu Elec.]", date(2026, 10, 25))
    assert states["A"]["Kartings"] == GameState(PANNE, 2, "Batterie")
    assert states["A"]["Air Hockey"].state == "maintenance"
    assert "Autre question" not in states["A"]


def test_taux_de_panne_et_trimestres():
    assert previous_quarter_start(date(2026, 11, 1)) == date(2026, 7, 1)
    assert previous_quarter_start(date(2026, 2, 1)) == date(2025, 10, 1)
    assert quarter_label(date(2026, 11, 1)) == "T4 2026"
    hebdos = [
        insp("A", date(2026, 7, 6), iid="a"),
        insp("B", date(2026, 7, 6), iid="b"),
        insp("A", date(2026, 10, 5), iid="c"),
        insp("A", date(2026, 8, 1), iid="d", completed=False),
    ]
    items = {
        "a": [{"label": KART, "response": "En Panne"}],
        "b": [{"label": KART, "response": "OK et En Service"}],
        "c": [{"label": KART, "response": "OK et En Service"}],
        "d": [{"label": KART, "response": "En Panne"}],  # non terminé : ignoré
    }
    rates = breakdown_rates(items, hebdos, "[Jeu Elec.]", date(2026, 7, 1), date(2026, 9, 30))
    assert rates == {"Kartings": (1, 2)}


def test_priorite():
    cells = [DayCell(ROUGE)] * 2 + [DayCell(JAUNE)] + [DayCell(VERT)] * 4
    p = priority(
        "A",
        cells,
        DayCell(ROUGE),
        {"Kartings": GameState(PANNE, 3)},
        ActionStats(a_traiter=4, anciennes=2),
    )
    # panne 5+2, hebdo 4, 2 manquants 4, 1 suspect 1.5, actions 3
    assert p.score == 19.5
    assert p.reasons[0] == "1 jeu en panne (Kartings 3 sem.)"
    big = priority(
        "B", [DayCell(VERT)] * 7, DayCell(VERT), {}, ActionStats(a_traiter=167, anciennes=157)
    )
    assert big.score == 8  # plafond
    calm = priority("C", [DayCell(VERT)] * 7, DayCell(VERT), {}, ActionStats())
    assert calm.score == 0 and calm.reasons == []


def test_tendances():
    k = Kpi("Jeux en panne", "4", 4, 6, "down")
    assert (k.trend, k.tone, k.delta) == ("down", "good", "-2 vs S-1")
    assert Kpi("x", "", 80, 70, "down", "%").delta == "+10 pts vs S-1"
    assert Kpi("x", "", 80, 70, "down").tone == "bad"
    assert Kpi("x", "", 5, 5, "up").trend == "flat"
    assert Kpi("x", "", 5, None, "up").trend == "none"


def test_regions_depuis_l_arbre_mitti():
    nodes = [
        {"id": "r1", "name": "Ouest", "meta_label": "region"},
        {"id": "r2", "name": "Est", "meta_label": "region"},
        {
            "id": "l1",
            "name": "gp-lma",
            "meta_label": "location",
            "parent_id": "r1",
            "site_uuid": "u1",
        },
        {"id": "l2", "name": "GP-BOR", "meta_label": "location", "parent_id": "r2"},
    ]
    ref = referentiel_from_sites(nodes)
    assert [(r.nom, r.parcs) for r in ref.regions] == [("Est", ["GP-BOR"]), ("Ouest", ["GP-LMA"])]
    assert ref.site_names == {"l1": "GP-LMA", "u1": "GP-LMA", "l2": "GP-BOR"}


def _journalier(i, site, day, hour=8, **kw):
    return {
        "id": f"j{i}",
        "site_id": site,
        "conducted_on": f"{day}T{hour:02d}:00:00Z",
        "date_started": f"{day}T{hour:02d}:00:00Z",
        "date_completed": f"{day}T{hour:02d}:20:00Z",
        "duration": 1200,
        "score_percentage": 95.0,
        "web_report_link": "https://r",
        **kw,
    }


def test_collecte_et_rendu(mitti, ss, router, report_settings, sync_settings):
    report_settings.excluded_sites = ["GP-TEST"]
    router.get(f"{MITTI}/feed/sites", params={"show_only_leaf_nodes": "false"}).respond(
        json={
            "data": [
                {"id": "r1", "name": "Carine (Nord)", "meta_label": "region"},
                {"id": "a1", "name": "Hauts-de-France", "meta_label": "area", "parent_id": "r1"},
                {"id": "s1", "name": "GP-A", "meta_label": "location", "parent_id": "a1"},
                {"id": "s2", "name": "GP-B", "meta_label": "location", "parent_id": "r1"},
                {"id": "s3", "name": "GP-C", "meta_label": "location"},
                {"id": "s4", "name": "GP-TEST", "meta_label": "location", "parent_id": "r1"},
                {"id": "s5", "name": "GP-OLD", "meta_label": "location", "deleted": True},
            ]
        }
    )
    router.get(f"{MITTI}/feed/inspections", params={"template": "tpl_j"}).respond(
        json={
            "data": [
                _journalier(1, "s1", "2026-10-19"),
                _journalier(2, "s2", "2026-10-19", duration=120),
                _journalier(3, "s3", "2026-10-20"),
                _journalier(5, "s1", "2026-10-21", hour=1),  # 3h du matin à Paris
                _journalier(4, "s1", "2026-10-12"),  # semaine précédente (S-1)
                _journalier(6, "s2", "2026-10-12"),
            ]
        }
    )
    router.get(f"{MITTI}/feed/inspections", params={"template": "tpl_h"}).respond(
        json={
            "data": [
                _journalier(9, "s1", "2026-10-21") | {"id": "h1", "duration": 3000},
                _journalier(8, "s1", "2026-08-10") | {"id": "h0", "duration": 3000},
            ]
        }
    )
    flipper = "[Jeu Elec.] Les Flippers sont-ils tous fonctionnels ?"
    router.get(f"{MITTI}/feed/inspection_items").respond(
        json={
            "data": [
                {"audit_id": "h1", "label": flipper, "response": "En Panne"},
                {"audit_id": "h0", "label": flipper, "response": "OK et En Service"},
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
                {"task": action("t0", "AC-0", "GP-A", STATUS_CANT_DO, "2026-10-13T10:00:00Z")},
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
                row(11, details="[AC-5] ~ q", start="2026-08-01"),
                row(12, details="[AC-7] ~ q", mgr=True, end="2026-10-22", start="2026-10-01"),
            ],
        )
    )

    rep = collect_weekly(
        mitti, SheetCache(ss, sync_settings), report_settings, sync_settings, MONDAY
    )

    assert [r.nom for r in rep.referentiel.regions] == ["Carine (Nord)", "Sans région"]
    assert rep.referentiel.regions[0].parcs == ["GP-A", "GP-B"]
    cur = rep.current
    assert cur.open_days == {date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)}
    a = cur.day_cells["GP-A"]
    assert [c.color for c in a[:4]] == [VERT, ROUGE, JAUNE, GRIS]
    assert a[2].issues == ["fait à 03h00"]
    b = cur.day_cells["GP-B"][0]
    assert b.color == JAUNE and b.issues[0].startswith("trop rapide 2 min")
    assert cur.hebdo["GP-A"].color == VERT and cur.hebdo["GP-B"].color == ROUGE
    assert cur.games["GP-A"]["Flippers"].state == PANNE

    s = cur.stats["GP-A"]
    assert (s.creees, s.transferees, s.rejetees) == (2, 1, 1)
    # AC-1 et AC-5 ouvertes ; AC-7 traitée dans la semaine
    assert (s.a_traiter, s.anciennes, s.traitees_semaine) == (2, 1, 1)
    assert cur.stats["GP-B"].ouvertes == 1
    # S-1 : AC-7 (Fin le 22/10) et AC-5 étaient ouvertes, AC-1 pas encore signalée
    assert rep.previous.stats["GP-A"].a_traiter == 2
    assert rep.previous.stats["GP-A"].rejetees == 1

    kpis = {k.label: k for k in rep.kpis}
    assert kpis["Jeux en panne"].current == 1 and kpis["Jeux en panne"].tone == "bad"
    assert kpis["Taux de rejet"].value == "33 %"

    assert rep.quarters == ("T3 2026", "T4 2026")
    flip = rep.machines[0]
    assert (flip.name, flip.prev, flip.cur, flip.down_now) == (
        "Flippers",
        (0, 1),
        (1, 1),
        [("GP-A", 1)],
    )
    assert rep.priorities[0].site == "GP-A"

    html = render(rep)
    assert subject(rep) == "Rapport maintenance du réseau du 19/10/2026 au 25/10/2026"
    for text in (
        "État du réseau",
        "Top 5 des parcs prioritaires",
        "Inspections de la semaine",
        "Focus actions",
        "Focus jeux électroniques",
        "Carine (Nord)",
        "cid:logo",
        "fait à 03h00",
        "T3 2026",
    ):
        assert text in html, text
    order = [
        html.index(t)
        for t in ("État du réseau", "Top 5", "Inspections de", "Focus actions", "Focus jeux")
    ]
    assert order == sorted(order)


def test_trames_non_configurees(mitti, ss, router, report_settings, sync_settings):
    report_settings.journalier_template_ids = []
    report_settings.hebdo_template_ids = []
    router.get(f"{MITTI}/feed/sites").respond(
        json={"data": [{"id": "s1", "name": "GP-A", "meta_label": "location"}]}
    )
    router.post(f"{MITTI}/tasks/v1/actions/list").respond(json={"actions": []})
    router.get(f"{SS}/sheets").respond(json={"data": []})
    rep = collect_weekly(
        mitti, SheetCache(ss, sync_settings), report_settings, sync_settings, MONDAY
    )
    assert rep.current.hebdo["GP-A"].text == "Non configuré"
    assert {c.color for c in rep.current.day_cells["GP-A"]} == {GRIS}
    assert rep.priorities[0].score == 0
    assert "Aucune réponse « jeux électroniques »" in render(rep)
