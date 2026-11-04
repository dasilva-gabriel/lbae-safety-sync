"""API Smartsheet (REST 2.0) et lecture des feuilles « Travaux … (CODE-PARC) ».

Depuis le 3 juin 2026, `includeAll` n'est plus pris en charge pour les listes :
on pagine (jeton `lastKey` si l'API le renvoie, sinon `page`/`totalPages`).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import httpx

from safetysync.config import SmartsheetSettings, SyncSettings
from safetysync.http import HttpApi

logger = logging.getLogger(__name__)

RE_UNIQUE_ID = re.compile(r"\[(AC-\d+)\]", re.IGNORECASE)


class SmartsheetAPI(HttpApi):
    service = "Smartsheet"

    def __init__(
        self, settings: SmartsheetSettings, http: httpx.Client | None = None, **kw
    ) -> None:
        super().__init__(
            settings.api_url,
            {"Authorization": f"Bearer {settings.api_token.get_secret_value()}"},
            timeout=settings.timeout_seconds,
            max_retries=settings.max_retries,
            http=http,
            **kw,
        )

    def list_sheets(self) -> list[tuple[int, str]]:
        sheets: list[tuple[int, str]] = []
        params: dict[str, Any] = {"paginationType": "token", "maxItems": 100}
        page = 1
        while True:
            data = self.json("GET", "/sheets", params=params)
            sheets += [(s["id"], s.get("name") or "") for s in data.get("data") or []]
            if data.get("lastKey"):
                params = {"paginationType": "token", "maxItems": 100, "lastKey": data["lastKey"]}
                continue
            total_pages = data.get("totalPages") or 1
            if "lastKey" not in data and page < total_pages:
                page += 1
                params = {"page": page, "pageSize": 100}
                continue
            break
        logger.info("Smartsheet : %s feuille(s) accessibles", len(sheets))
        return sheets

    def get_sheet(self, sheet_id: int) -> dict:
        return self.json("GET", f"/sheets/{sheet_id}")

    def add_rows(self, sheet_id: int, rows: list[dict]) -> list[dict]:
        data = self.json("POST", f"/sheets/{sheet_id}/rows", json=rows)
        return data.get("result") or []

    def update_rows(self, sheet_id: int, rows: list[dict]) -> list[dict]:
        data = self.json("PUT", f"/sheets/{sheet_id}/rows", json=rows)
        return data.get("result") or []

    def attach_file(
        self, sheet_id: int, row_id: int, filename: str, content: bytes, content_type: str
    ) -> None:
        self.request(
            "POST",
            f"/sheets/{sheet_id}/rows/{row_id}/attachments",
            content=content,
            headers={
                "Content-Type": content_type,
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )


# ---------------------------------------------------------------------------
def normalize(text: Any) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return " ".join(text.lower().split())


def find_travaux_sheets(
    sheets: list[tuple[int, str]], pattern: str
) -> tuple[dict[str, tuple[int, str]], list[str]]:
    """CODE-PARC -> (id, nom). Renvoie aussi les doublons (gardés : le premier)."""
    regex = re.compile(pattern)
    mapping: dict[str, tuple[int, str]] = {}
    duplicates: list[str] = []
    for sheet_id, name in sheets:
        match = regex.search(name or "")
        if not match:
            continue
        site = match.group("site").strip().upper()
        if site in mapping:
            duplicates.append(f"{site} : « {mapping[site][1]} » et « {name} »")
            continue
        mapping[site] = (sheet_id, name)
    return mapping, duplicates


@dataclass
class SheetRow:
    id: int
    values: dict[int, Any]
    created_at: datetime | None = None

    def get(self, column_id: int | None) -> Any:
        return self.values.get(column_id) if column_id else None


@dataclass
class TravauxSheet:
    """Vue d'une feuille Travaux : colonnes utiles, ligne de section et lignes par n° AC."""

    id: int
    name: str
    columns: dict[str, int | None]
    rows: list[SheetRow]
    section_row_id: int | None
    by_unique_id: dict[str, SheetRow] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict, settings: SyncSettings) -> TravauxSheet:
        wanted = {
            "site": settings.col_site,
            "name": settings.col_name,
            "details": settings.col_details,
            "start": settings.col_start,
            "end": settings.col_end,
            "status_mgr": settings.col_status_mgr,
            "comment_mgr": settings.col_comment_mgr,
            "reporter": settings.col_reporter,
            "report_link": settings.col_report_link,
        }
        columns: dict[str, int | None] = dict.fromkeys(wanted)
        for col in data.get("columns") or []:
            title = normalize(col.get("title"))
            for key, expected in wanted.items():
                exp = normalize(expected)
                if columns[key] is None and (title == exp or title.startswith(exp)):
                    columns[key] = col["id"]
                    break

        rows = []
        section_row_id = None
        section = normalize(settings.section_title)
        for raw in data.get("rows") or []:
            values = {c["columnId"]: c.get("value") for c in raw.get("cells") or []}
            created = raw.get("createdAt")
            row = SheetRow(
                id=raw["id"],
                values=values,
                created_at=_parse_dt(created),
            )
            rows.append(row)
            if section_row_id is None and normalize(row.get(columns["name"])) == section:
                section_row_id = row.id
        if section_row_id is None:
            # Ancien comportement : la section peut être dans une autre colonne.
            for row in rows:
                if any(normalize(v) == section for v in row.values.values()):
                    section_row_id = row.id
                    break

        sheet = cls(
            id=data["id"],
            name=data.get("name") or "",
            columns=columns,
            rows=rows,
            section_row_id=section_row_id,
        )
        for row in rows:
            uid = extract_unique_id(row.get(columns["details"]))
            if uid and uid not in sheet.by_unique_id:
                sheet.by_unique_id[uid] = row
        return sheet

    def ac_rows(self) -> Iterator[tuple[str, SheetRow]]:
        yield from self.by_unique_id.items()

    def is_done(self, row: SheetRow) -> bool:
        return bool(row.get(self.columns["status_mgr"]))


def extract_unique_id(value: Any) -> str | None:
    match = RE_UNIQUE_ID.search(str(value or ""))
    return match.group(1).upper() if match else None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
