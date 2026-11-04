"""Référentiel des régions et parcs (fichier TOML, voir parcs.example.toml)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Region:
    nom: str
    parcs: list[str]
    responsable: str = ""
    emails: list[str] = field(default_factory=list)


@dataclass
class Referentiel:
    regions: list[Region]

    @property
    def parcs(self) -> list[str]:
        return [p for r in self.regions for p in r.parcs]

    def region_of(self, parc: str) -> Region | None:
        for region in self.regions:
            if parc in region.parcs:
                return region
        return None

    def with_unknown(self, seen: set[str]) -> Referentiel:
        """Ajoute une région « Autres parcs » pour les sites vus mais non référencés."""
        known = set(self.parcs)
        others = sorted(s for s in seen if s and s not in known)
        if not others:
            return self
        return Referentiel(
            self.regions + [Region(nom="Autres parcs (non référencés)", parcs=others)]
        )


def load_referentiel(path: Path) -> Referentiel:
    if not path.exists():
        return Referentiel(regions=[])
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    regions = []
    for raw in data.get("regions") or []:
        regions.append(
            Region(
                nom=raw["nom"],
                parcs=[p.strip().upper() for p in raw.get("parcs") or []],
                responsable=raw.get("responsable", ""),
                emails=list(raw.get("emails") or []),
            )
        )
    return Referentiel(regions=regions)
