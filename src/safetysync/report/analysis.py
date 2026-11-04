"""Calculs du rapport hebdomadaire (fonctions pures, testables sans API).

Règles détaillées dans docs/fonctionnement.md (RAP-xx).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# Couleurs des cellules (légende du rapport)
VERT, JAUNE, ROUGE, BLEU, GRIS = "vert", "jaune", "rouge", "bleu", "gris"

# États des jeux électroniques
OK, MAINTENANCE, PANNE, INCONNU = "ok", "maintenance", "panne", "inconnu"


@dataclass
class Inspection:
    id: str
    site: str
    day: date  # jour contrôlé (« Effectué le », sinon date de début)
    started_day: date | None
    completed: bool
    duration: int | None  # secondes
    score: float | None  # pourcentage
    link: str = ""

    @classmethod
    def from_feed(cls, raw: dict, site: str, tz: ZoneInfo) -> Inspection | None:
        conducted = _dt(raw.get("conducted_on"))
        started = _dt(raw.get("date_started")) or _dt(raw.get("created_at"))
        ref = conducted or started
        if ref is None:
            return None
        score = raw.get("score_percentage")
        return cls(
            id=raw.get("id") or "",
            site=site,
            day=ref.astimezone(tz).date(),
            started_day=started.astimezone(tz).date() if started else None,
            completed=bool(raw.get("date_completed")),
            duration=int(raw["duration"]) if raw.get("duration") not in (None, "") else None,
            score=float(score) if score not in (None, "") else None,
            link=raw.get("web_report_link") or "",
        )


@dataclass
class DayCell:
    color: str
    text: str
    issues: list[str] = field(default_factory=list)
    link: str = ""


def week_days(monday: date) -> list[date]:
    return [monday + timedelta(days=i) for i in range(7)]


def opening_days(
    days: list[date], journaliers: list[Inspection], parcs: list[str], ratio: float
) -> set[date]:
    """RAP-02 : un jour est ouvert si au moins `ratio` des parcs ont fait leur journalier."""
    done: dict[date, set[str]] = defaultdict(set)
    for insp in journaliers:
        done[insp.day].add(insp.site)
    reference = set(parcs) or {i.site for i in journaliers}
    total = max(len(reference), 1)
    return {d for d in days if done[d] and len(done[d] & reference) / total >= ratio}


def day_cell(inspections: list[Inspection], is_open: bool, min_duration: int) -> DayCell:
    """RAP-03 : couleur et texte d'une cellule (parc × jour)."""
    if not inspections:
        if is_open:
            return DayCell(ROUGE, "Pas de journalier")
        return DayCell(GRIS, "Fermé")
    completed = [i for i in inspections if i.completed]
    if not completed:
        return DayCell(
            JAUNE, "Inspection incomplète", ["Inspection incomplète"], inspections[-1].link
        )

    last = completed[-1]
    issues: list[str] = []
    durations = [i.duration for i in completed if i.duration is not None]
    if durations and max(durations) < min_duration:
        issues.append(f"Durée non cohérente ({max(durations)} sec)")
    scores = sorted({round(i.score, 1) for i in completed if i.score is not None})
    if len(scores) > 1:
        issues.append(f"Score non cohérent ({_pct(scores[0])} à {_pct(scores[-1])})")
    late = [i for i in completed if i.started_day and i.started_day > i.day]
    if late:
        issues.append(f"Saisie a posteriori (le {late[-1].started_day:%d/%m})")

    text = _pct(last.score) if last.score is not None else "Fait"
    if issues:
        return DayCell(JAUNE, text, issues, last.link)
    early = [i for i in completed if i.started_day and i.started_day < i.day]
    if early:
        return DayCell(
            BLEU, text, [f"Anticipée (faite le {early[-1].started_day:%d/%m})"], last.link
        )
    return DayCell(VERT, text, [], last.link)


def hebdo_cell(inspections: list[Inspection]) -> DayCell:
    """RAP-04 : contrôle hebdomadaire de la semaine."""
    completed = [i for i in inspections if i.completed]
    if completed:
        n = len(completed)
        return DayCell(VERT, f"{n} trouvé{'s' if n > 1 else ''}", [], completed[-1].link)
    if inspections:
        return DayCell(JAUNE, "Incomplet", ["Hebdo incomplet"], inspections[-1].link)
    return DayCell(ROUGE, "Manquant", ["Pas d'hebdo"])


# ------------------------------------------------------------- jeux électroniques
@dataclass
class GameState:
    state: str
    weeks_down: int = 0  # nb d'hebdos consécutifs « En Panne » (dernier compris)
    comment: str = ""


def response_state(response: str) -> str:
    text = (response or "").lower()
    if "panne" in text:
        return PANNE
    if "maintenance" in text:
        return MAINTENANCE
    if text.startswith("ok"):
        return OK
    return INCONNU


_GAME_CUTS = re.compile(r"\s+(sont-ils|sont-elles|est-il|est-elle|sont|est)\b.*$", re.IGNORECASE)
_GAME_PREFIX = re.compile(
    r"^(\d+(\.\d+)*\.\s*)?(v[ée]rification de (tous )?(les )?|les |le |la |l')", re.IGNORECASE
)


def is_elec(label: str, prefix: str) -> bool:
    """Question « jeux électroniques » (préfixe présent, même après une numérotation)."""
    return prefix.lower() in (label or "").lower()


def game_name(label: str, prefix: str) -> str:
    """« [Jeu Elec.] Les Air Hockey sont-ils tous fonctionnels ? » → « Air Hockey »."""
    text = (label or "").strip()
    index = text.lower().find(prefix.lower())
    if index >= 0:
        text = text[index + len(prefix) :].strip()
    text = _GAME_PREFIX.sub("", text)
    text = _GAME_CUTS.sub("", text).strip(" ?")
    return text[:1].upper() + text[1:]


def game_states(
    items_by_inspection: dict[str, list[dict]],
    hebdos: list[Inspection],
    prefix: str,
    until: date,
) -> dict[str, dict[str, GameState]]:
    """RAP-06 : état de chaque jeu par parc d'après le dernier hebdo terminé (≤ `until`)."""
    by_site: dict[str, list[Inspection]] = defaultdict(list)
    for insp in sorted(hebdos, key=lambda i: i.day):
        if insp.completed and insp.day <= until and insp.id in items_by_inspection:
            by_site[insp.site].append(insp)

    result: dict[str, dict[str, GameState]] = {}
    for site, history in by_site.items():
        timeline: list[dict[str, tuple[str, str]]] = []
        for insp in history:
            states = {}
            for item in items_by_inspection[insp.id]:
                label = item.get("label") or ""
                if not is_elec(label, prefix):
                    continue
                states[game_name(label, prefix)] = (
                    response_state(item.get("response") or ""),
                    (item.get("comment") or "").strip(),
                )
            timeline.append(states)
        latest = timeline[-1]
        site_states = {}
        for game, (state, comment) in latest.items():
            weeks = 0
            if state == PANNE:
                for states in reversed(timeline):
                    if states.get(game, (None,))[0] == PANNE:
                        weeks += 1
                    else:
                        break
            site_states[game] = GameState(state, weeks, comment)
        result[site] = site_states
    return result


# ---------------------------------------------------------------------- actions
@dataclass
class ActionStats:
    creees: int = 0  # actions Mitti créées dans la semaine
    transferees: int = 0  # … passées « In progress » ou « Complete »
    rejetees: int = 0  # … passées « Can't do »
    ouvertes: int = 0  # actions Mitti encore « To do » (toutes, période de transfert)
    a_traiter: int = 0  # lignes AC non cochées dans Smartsheet
    anciennes: int = 0  # … dont signalées depuis plus de N jours
    traitees_semaine: int = 0  # lignes AC cochées avec « Fin » dans la semaine
    plus_ancienne: date | None = None


# ---------------------------------------------------------------------- priorité
@dataclass
class Priority:
    site: str
    score: float
    reasons: list[str]


def priority(
    site: str,
    cells: list[DayCell],
    hebdo: DayCell,
    games: dict[str, GameState],
    stats: ActionStats,
) -> Priority:
    """RAP-08 : score de priorité d'un parc (plus il est haut, plus le parc est prioritaire)."""
    score = 0.0
    reasons: list[str] = []
    pannes = [(g, s) for g, s in games.items() if s.state == PANNE]
    if pannes:
        score += sum(5 + min(s.weeks_down - 1, 4) for _, s in pannes)
        reasons.append(
            f"{len(pannes)} jeu(x) en panne ("
            + ", ".join(
                f"{g}{f' depuis {s.weeks_down} sem.' if s.weeks_down > 1 else ''}"
                for g, s in pannes
            )
            + ")"
        )
    maint = [g for g, s in games.items() if s.state == MAINTENANCE]
    if maint:
        score += len(maint)
        reasons.append(f"{len(maint)} jeu(x) à maintenir")
    if hebdo.color == ROUGE:
        score += 4
        reasons.append("hebdo manquant")
    elif hebdo.color == JAUNE:
        score += 2
        reasons.append("hebdo incomplet")
    missing = sum(1 for c in cells if c.color == ROUGE)
    doubtful = sum(1 for c in cells if c.color == JAUNE)
    if missing:
        score += 2 * missing
        reasons.append(f"{missing} journalier(s) manquant(s)")
    if doubtful:
        score += doubtful
        reasons.append(f"{doubtful} journalier(s) douteux")
    if stats.a_traiter:
        score += 0.5 * stats.a_traiter + 0.5 * stats.anciennes
        txt = f"{stats.a_traiter} action(s) à traiter"
        if stats.anciennes:
            txt += f" dont {stats.anciennes} ancienne(s)"
        reasons.append(txt)
    if stats.ouvertes:
        score += 0.5 * stats.ouvertes
        reasons.append(f"{stats.ouvertes} action(s) Mitti non transférée(s)")
    return Priority(site, round(score, 1), reasons)


# ---------------------------------------------------------------------- helpers
def _pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.1f}".rstrip("0").rstrip(".") + "%"


def _dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
