from datetime import UTC, datetime

import httpx
from conftest import MITTI, SS, action, body

from safetysync.mitti import STATUS_COMPLETE, STATUS_TODO, find_valid_reference
from safetysync.smartsheet import find_travaux_sheets


def test_iter_actions_lit_toutes_les_pages(mitti, router):
    route = router.post(f"{MITTI}/tasks/v1/actions/list")
    route.side_effect = [
        httpx.Response(
            200,
            json={
                "actions": [
                    {"task": action("t1", "AC-1", "GP-A", STATUS_TODO)},
                    {"task": action("t2", "AC-2", "GP-A", STATUS_TODO)},
                ],
                "next_page_token": "p2",
            },
        ),
        httpx.Response(
            200,
            json={
                "actions": [
                    {"task": action("t3", "AC-3", "GP-B", STATUS_TODO)},
                    {"task": action("t4", "AC-4", "GP-B", STATUS_COMPLETE)},
                ],
                "next_page_token": "p3",
            },
        ),
        httpx.Response(
            200,
            json={
                "actions": [
                    {
                        "task": action(
                            "t5", "AC-5", "GP-B", STATUS_TODO, created="2026-01-01T00:00:00Z"
                        )
                    }
                ]
            },
        ),
    ]
    tasks = list(
        mitti.iter_actions(status_ids=[STATUS_TODO], created_from=datetime(2026, 10, 1, tzinfo=UTC))
    )
    # 3 pages lues ; AC-4 (mauvais statut) et AC-5 (trop ancienne) filtrées côté client
    assert [t["unique_id"] for t in tasks] == ["AC-1", "AC-2", "AC-3"]
    assert route.call_count == 3
    first, second = body(route.calls[0]), body(route.calls[1])
    assert "page_token" not in first and second["page_token"] == "p2"
    assert first["page_size"] == 2
    assert first["task_filters"][0]["status_id"]["value"] == [STATUS_TODO]
    assert first["task_filters"][1]["created_at"]["from"]["value"] == "2026-10-01T00:00:00.000Z"


def test_retry_sur_429(mitti, router):
    route = router.get(f"{MITTI}/tasks/v1/actions/t1")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(200, json={"action": {"task": {"task_id": "t1"}}}),
    ]
    assert mitti.get_action("t1") == {"task_id": "t1"}
    assert route.call_count == 2


def test_feed_suit_next_page(mitti, router):
    router.get(f"{MITTI}/feed/sites", params={"show_only_leaf_nodes": "false"}).respond(
        json={"data": [{"id": "s1"}], "metadata": {"next_page": "/feed/sites?offset=1"}}
    )
    router.get(f"{MITTI}/feed/sites", params={"offset": "1"}).respond(
        json={"data": [{"id": "s2"}], "metadata": {"next_page": None}}
    )
    assert [s["id"] for s in mitti.feed_sites()] == ["s1", "s2"]


def test_reference_valide():
    task = {
        "references": [
            {
                "inspection_context": {
                    "list_answer": {"responses": [{"label": "Signalé dans l'Hebdo"}]}
                }
            },
            {"inspection_context": {"list_answer": {"responses": [{"label": " en panne "}]}}},
        ]
    }
    assert find_valid_reference(task, ["En Panne"]) is task["references"][1]
    assert find_valid_reference(task, ["Premier Signalement"]) is None


def test_list_sheets_pagination_par_jeton(ss, router):
    route = router.get(f"{SS}/sheets")
    route.side_effect = [
        httpx.Response(200, json={"data": [{"id": 1, "name": "A"}], "lastKey": "k1"}),
        httpx.Response(200, json={"data": [{"id": 2, "name": "B"}]}),
    ]
    assert ss.list_sheets() == [(1, "A"), (2, "B")]
    assert route.calls[1].request.url.params["lastKey"] == "k1"


def test_list_sheets_pagination_par_page(ss, router):
    route = router.get(f"{SS}/sheets")
    route.side_effect = [
        httpx.Response(
            200, json={"data": [{"id": 1, "name": "A"}], "pageNumber": 1, "totalPages": 2}
        ),
        httpx.Response(
            200, json={"data": [{"id": 2, "name": "B"}], "pageNumber": 2, "totalPages": 2}
        ),
    ]
    assert ss.list_sheets() == [(1, "A"), (2, "B")]
    assert route.calls[1].request.url.params["page"] == "2"


def test_feuilles_travaux():
    sheets = [
        (1, "Travaux GP Le Mans (GP-LMA)"),
        (2, "Travaux GP Le Havre (gp-lhv)"),
        (3, "Planning (GP-LMA)"),
        (4, "Copie de Travaux GP Le Mans (GP-LMA)"),
    ]
    mapping, dup = find_travaux_sheets(sheets, r"(?i)\btravaux\b.*\((?P<site>[^)]+)\)\s*$")
    assert mapping == {
        "GP-LMA": (1, "Travaux GP Le Mans (GP-LMA)"),
        "GP-LHV": (2, "Travaux GP Le Havre (gp-lhv)"),
    }
    assert len(dup) == 1
