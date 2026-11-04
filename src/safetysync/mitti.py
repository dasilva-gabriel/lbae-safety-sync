"""API Mitti (ex-SafetyCulture) : actions, commentaires, médias et data feeds.

Doc : https://developer.mitti.com/reference
- Liste des actions : POST /tasks/v1/actions/list, pagination par `next_page_token`
  (100 actions max par page). C'était le bug des anciens scripts : seules les
  100 dernières actions étaient lues.
- Data feeds (/feed/...) : pagination par `metadata.next_page`.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

import httpx

from safetysync.config import MittiSettings
from safetysync.http import HttpApi

logger = logging.getLogger(__name__)

# Statuts standard des actions (IDs fixes documentés par Mitti).
STATUS_TODO = "17e793a1-26a3-4ecd-99ca-f38ecc6eaa2e"
STATUS_IN_PROGRESS = "20ce0cb1-387a-47d4-8c34-bc6fd3be0e27"
STATUS_COMPLETE = "7223d809-553e-4714-a038-62dc98f3fbf3"
STATUS_CANT_DO = "06308884-41c2-4ee0-9da7-5676647d3d75"

STATUS_LABELS = {
    STATUS_TODO: "To do",
    STATUS_IN_PROGRESS: "In progress",
    STATUS_COMPLETE: "Complete",
    STATUS_CANT_DO: "Can't do",
}


class MittiAPI(HttpApi):
    service = "Mitti"

    def __init__(self, settings: MittiSettings, http: httpx.Client | None = None, **kw) -> None:
        super().__init__(
            settings.api_url,
            {
                "Authorization": f"Bearer {settings.api_token.get_secret_value()}",
                "accept": "application/json",
            },
            timeout=settings.timeout_seconds,
            max_retries=settings.max_retries,
            http=http,
            **kw,
        )
        self.settings = settings

    # ------------------------------------------------------------------ actions
    def iter_actions(
        self,
        *,
        status_ids: list[str] | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        sort_direction: str = "DESC",
    ) -> Iterator[dict]:
        """Itère sur TOUTES les actions correspondant aux filtres (toutes les pages).

        Renvoie les objets `task`. Les filtres sont aussi appliqués côté client par
        sécurité (au cas où l'API ignorerait un filtre).
        """
        filters: list[dict] = []
        if status_ids:
            filters.append({"status_id": {"operator": "FILTER_OPERATOR_IN", "value": status_ids}})
        if created_from or created_to:
            date_filter: dict[str, Any] = {}
            if created_from:
                date_filter["from"] = _date_bound(created_from)
            if created_to:
                date_filter["to"] = _date_bound(created_to)
            filters.append({"created_at": date_filter})

        body: dict[str, Any] = {
            "page_size": self.settings.page_size,
            "sort_field": "CREATED_AT",
            "sort_direction": sort_direction,
            "without_count": True,
        }
        if filters:
            body["task_filters"] = filters

        seen_tokens: set[str] = set()
        pages = total = 0
        while True:
            data = self.json("POST", "/tasks/v1/actions/list", json=body)
            pages += 1
            for action in data.get("actions") or []:
                task = (action or {}).get("task") or {}
                if not task.get("task_id"):
                    continue
                if status_ids and task_status_id(task) not in status_ids:
                    continue
                created = parse_dt(task.get("created_at"))
                if created_from and created and created < created_from:
                    continue
                if created_to and created and created > created_to:
                    continue
                total += 1
                yield task
            token = data.get("next_page_token")
            if not token or token in seen_tokens:
                break
            seen_tokens.add(token)
            body["page_token"] = token
        logger.info("Mitti : %s action(s) lue(s) sur %s page(s)", total, pages)

    def get_action(self, task_id: str) -> dict:
        data = self.json("GET", f"/tasks/v1/actions/{task_id}")
        return (data.get("action") or {}).get("task") or data.get("task") or {}

    def set_status(self, task_id: str, status_id: str) -> None:
        self.json("PUT", f"/tasks/v1/actions/{task_id}/status", json={"status_id": status_id})

    def add_comment(self, task_id: str, comment: str) -> None:
        body = {"task_id": task_id, "comment": comment}
        self.json("POST", "/tasks/v1/timeline/comments", json=body)

    # ------------------------------------------------------------------- médias
    def media_url(self, media: Mapping[str, Any], filename: str) -> str:
        params = {"token": media.get("token"), "download_as_attachment_name": filename}
        if media.get("media_type"):
            params["media_type"] = media["media_type"]
        data = self.json("GET", f"/media/v1/download/{media['id']}", params=params)
        return data.get("url") or ""

    def download(self, url: str) -> tuple[bytes, str]:
        """Télécharge une URL signée (sans le token Mitti). Renvoie (contenu, content-type)."""
        resp = self._http.get(url, follow_redirects=True)
        resp.raise_for_status()
        return resp.content, resp.headers.get("content-type", "application/octet-stream")

    # --------------------------------------------------------------- data feeds
    def iter_feed(self, path: str, params: Any = None) -> Iterator[dict]:
        next_path: str | None = path
        first = True
        while next_path:
            data = self.json("GET", next_path, params=params if first else None)
            first = False
            yield from data.get("data") or []
            following = (data.get("metadata") or {}).get("next_page")
            if not following or following == next_path:
                break
            next_path = following

    def feed_inspections(
        self, template_ids: list[str], modified_after: datetime, *, completed: str = "both"
    ) -> Iterator[dict]:
        params = [("modified_after", _iso(modified_after)), ("completed", completed)]
        params += [("template", t) for t in template_ids]
        return self.iter_feed("/feed/inspections", params)

    def feed_inspection_items(
        self, template_ids: list[str], modified_after: datetime
    ) -> Iterator[dict]:
        params = [("modified_after", _iso(modified_after)), ("completed", "true")]
        params += [("template", t) for t in template_ids]
        return self.iter_feed("/feed/inspection_items", params)

    def feed_sites(self, *, leaf_only: bool = False) -> Iterator[dict]:
        """Sites Mitti. Par défaut tout l'arbre (location, area, region…) pour les régions."""
        flag = "true" if leaf_only else "false"
        return self.iter_feed("/feed/sites", {"show_only_leaf_nodes": flag})


# ---------------------------------------------------------------------- helpers
def task_status_id(task: Mapping[str, Any]) -> str | None:
    status = task.get("status") or {}
    return status.get("status_id") or task.get("status_id")


def task_site(task: Mapping[str, Any]) -> str:
    return ((task.get("site") or {}).get("name") or "").strip().upper()


def task_creator(task: Mapping[str, Any]) -> str:
    creator = task.get("creator") or {}
    return " ".join(p for p in (creator.get("firstname"), creator.get("lastname")) if p).strip()


def find_valid_reference(task: Mapping[str, Any], labels: list[str]) -> dict | None:
    """Référence d'inspection dont la réponse fait partie des `labels` (ex. Premier Signalement)."""
    wanted = {label.strip().lower() for label in labels}
    for ref in task.get("references") or []:
        ctx = ref.get("inspection_context") or {}
        for response in (ctx.get("list_answer") or {}).get("responses") or []:
            if (response.get("label") or "").strip().lower() in wanted:
                return ref
    return None


def has_inspection_reference(task: Mapping[str, Any]) -> bool:
    return any(ref.get("inspection_context") for ref in task.get("references") or [])


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(value: datetime) -> str:
    """Format attendu par l'API : 2014-01-28T23:14:23.000Z (UTC)."""
    if value.tzinfo is None:
        value = value.astimezone()
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _date_bound(value: datetime) -> dict[str, str]:
    return {"operator": "FILTER_OPERATOR_EQ", "value": _iso(value)}
