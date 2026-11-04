"""Référentiel des régions et parcs, construit depuis l'arbre des sites Mitti.

Le data feed /feed/sites (avec `show_only_leaf_nodes=false`) renvoie tous les niveaux
(location, area, region…) avec `parent_id` et `meta_label`. Un parc est un site
« feuille » ; sa région est le premier ancêtre dont le niveau vaut `region_level`
(par défaut « area », le niveau qui porte le nom du DR dans Mitti), à défaut l'ancêtre direct.
"""

from __future__ import annotations

from dataclasses import dataclass

NO_REGION = "Sans région"
UNKNOWN_REGION = "Autres parcs (hors arbre Mitti)"


@dataclass
class Region:
    nom: str
    parcs: list[str]


@dataclass
class Referentiel:
    regions: list[Region]
    site_names: dict[str, str]  # id du site Mitti -> code parc (ex. GP-LMA)

    @property
    def parcs(self) -> list[str]:
        return [p for r in self.regions for p in r.parcs]

    def region_of(self, parc: str) -> Region | None:
        for region in self.regions:
            if parc in region.parcs:
                return region
        return None

    def with_unknown(self, seen: set[str]) -> Referentiel:
        """Ajoute les parcs vus (actions, feuilles…) mais absents de l'arbre Mitti."""
        known = set(self.parcs)
        others = sorted(s for s in seen if s and s not in known)
        if not others:
            return self
        return Referentiel(self.regions + [Region(UNKNOWN_REGION, others)], self.site_names)


def referentiel_from_sites(
    nodes: list[dict], *, region_level: str = "area", excluded: list[str] | None = None
) -> Referentiel:
    excluded_set = {e.strip().upper() for e in excluded or []}
    alive = [n for n in nodes if not n.get("deleted")]
    by_id = {n["id"]: n for n in alive if n.get("id")}
    parents = {n.get("parent_id") for n in alive if n.get("parent_id")}
    level = region_level.strip().lower()

    def is_leaf(node: dict) -> bool:
        label = (node.get("meta_label") or "").lower()
        if label:
            return label == "location"
        return node["id"] not in parents

    def region_name(node: dict) -> str:
        parent = by_id.get(node.get("parent_id") or "")
        first = parent
        seen: set[str] = set()
        while parent and parent["id"] not in seen:
            seen.add(parent["id"])
            if (parent.get("meta_label") or "").lower() == level:
                return (parent.get("name") or "").strip() or NO_REGION
            parent = by_id.get(parent.get("parent_id") or "")
        return ((first or {}).get("name") or "").strip() or NO_REGION

    groups: dict[str, list[str]] = {}
    site_names: dict[str, str] = {}
    for node in alive:
        if not node.get("id") or not is_leaf(node):
            continue
        code = (node.get("name") or "").strip().upper()
        if not code:
            continue
        site_names[node["id"]] = code
        if node.get("site_uuid"):
            site_names[node["site_uuid"]] = code
        if code in excluded_set:
            continue
        groups.setdefault(region_name(node), []).append(code)

    regions = [
        Region(name, sorted(set(codes)))
        for name, codes in sorted(groups.items(), key=lambda kv: (kv[0] == NO_REGION, kv[0]))
    ]
    return Referentiel(regions, site_names)
