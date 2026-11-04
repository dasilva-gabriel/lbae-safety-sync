"""Calculs du rapport hebdomadaire (fonctions pures, testables sans API).

Règles détaillées dans docs/fonctionnement.md (RAP-xx).
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# Couleurs des cellules (légende du rapport)
# vert = fait et cohérent, jaune = incohérent, orange = incomplet (non clôturé),
# rouge = manquant, bleu = antidaté (saisi après le jour contrôlé), gris = fermé
VERT, JAUNE, ORANGE, ROUGE, BLEU, GRIS = "vert", "jaune", "orange", "rouge", "bleu", "gris"
SYMBOLS = {VERT: "✓", JAUNE: "!", ORANGE: "…", ROUGE: "✗", BLEU: "A", GRIS: "–"}
STATE_LABELS = {
    VERT: "Fait, cohérent",
    JAUNE: "Incohérent",
    ORANGE: "Incomplet / non clôturé",
    ROUGE: "Manquant",
    BLEU: "Antidaté",
    GRIS: "Fermé",
}
# Contrôle clôturé (compte comme « fait »)
DONE_COLORS = (VERT, JAUNE, BLEU)

# États des jeux électroniques
OK, MAINTENANCE, PANNE, INCONNU = "ok", "maintenance", "panne", "inconnu"


@dataclass
class Inspection:
    id: str
    site: str
    day: date  # jour contrôlé (« Effectué le », sinon date de début)
    started: datetime | None  # début réel, heure locale
    completed: bool
    duration: int | None  # secondes
    score: float | None  # pourcentage
    link: str = ""

    @property
    def started_day(self) -> date | None:
        return self.started.date() if self.started else None

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
            started=started.astimezone(tz) if started else None,
            completed=bool(raw.get("date_completed")),
            duration=int(float(raw["duration"])) if raw.get("duration") not in (None, "") else None,
            score=float(score) if score not in (None, "") else None,
            link=raw.get("web_report_link") or "",
        )


@dataclass
class DayCell:
    color: str
    text: str = ""
    issues: list[str] = field(default_factory=list)
    link: str = ""
    score: float | None = None

    @property
    def symbol(self) -> str:
        return SYMBOLS[self.color]


NOT_CONFIGURED = DayCell(GRIS, "Non configuré")


@dataclass
class Thresholds:
    """Seuils d'incohérence d'une semaine (RAP-03)."""

    min_duration: int  # durée sous laquelle un contrôle est « trop rapide »
    median: int | None = None  # durée médiane réseau de la semaine
    hour_start: int = 6
    hour_end: int = 23
    score_jump: float = 20.0  # écart de note (points) avec le contrôle précédent


def thresholds(
    inspections: list[Inspection],
    min_seconds: int,
    ratio: float,
    hour_start=6,
    hour_end=23,
    score_jump: float = 20.0,
) -> Thresholds:
    """Seuil « trop rapide » = max(minimum absolu, ratio × médiane réseau de la semaine)."""
    durations = [i.duration for i in inspections if i.completed and i.duration]
    median = int(statistics.median(durations)) if durations else None
    limit = max(min_seconds, int(ratio * median) if median else 0)
    return Thresholds(limit, median, hour_start, hour_end, score_jump)


Previous = tuple[float, date]  # (note, jour) du contrôle clôturé précédent du même parc


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


def _incoherences(
    completed: list[Inspection], th: Thresholds, open_issues: int, previous: Previous | None
) -> list[str]:
    issues: list[str] = []
    durations = [i.duration for i in completed if i.duration is not None]
    if durations and max(durations) < th.min_duration:
        med = f" (méd. {fmt_duration(th.median)})" if th.median else ""
        issues.append(f"trop rapide {fmt_duration(max(durations))}{med}")
    scores = sorted({round(i.score, 1) for i in completed if i.score is not None})
    if len(scores) > 1:
        issues.append(f"scores {pct(scores[0])}→{pct(scores[-1])}")
    early = [i for i in completed if i.started_day and i.started_day < i.day]
    if early:
        issues.append(f"anticipé le {early[-1].started_day:%d/%m}")
    odd = [
        i for i in completed if i.started and not (th.hour_start <= i.started.hour < th.hour_end)
    ]
    if odd:
        issues.append(f"fait à {odd[-1].started:%Hh%M}")
    last = completed[-1]
    if open_issues and last.score is not None and last.score >= 99.95:
        issues.append(f"100 % malgré {open_issues} pb ouvert{'s' if open_issues > 1 else ''}")
    if previous and last.score is not None and abs(last.score - previous[0]) >= th.score_jump:
        issues.append(
            f"note {last.score:.0f} % (contre {previous[0]:.0f} % le {previous[1]:%d/%m})"
        )
    return issues


def _closed_cell(
    completed: list[Inspection],
    th: Thresholds | None,
    open_issues: int,
    previous: Previous | None = None,
) -> DayCell:
    """Contrôle clôturé : antidaté (bleu) > incohérent (jaune) > cohérent (vert)."""
    last = completed[-1]
    text = pct(last.score) if last.score is not None else "Fait"
    scores = [i.score for i in completed if i.score is not None]
    score = sum(scores) / len(scores) if scores else None
    issues = _incoherences(completed, th, open_issues, previous) if th else []
    late = [i for i in completed if i.started_day and i.started_day > i.day]
    if late:
        issues = [f"saisi le {late[-1].started_day:%d/%m}"] + issues
        return DayCell(BLEU, text, issues, last.link, score)
    return DayCell(JAUNE if issues else VERT, text, issues, last.link, score)


def day_cell(
    inspections: list[Inspection],
    is_open: bool,
    th: Thresholds,
    open_issues: int = 0,
    previous: Previous | None = None,
) -> DayCell:
    """RAP-03 : état d'une cellule (parc × jour)."""
    if not inspections:
        return DayCell(ROUGE, "Pas de journalier") if is_open else DayCell(GRIS, "Fermé")
    completed = [i for i in inspections if i.completed]
    if not completed:
        return DayCell(ORANGE, "Non clôturé", ["non clôturé"], inspections[-1].link)
    return _closed_cell(completed, th, open_issues, previous)


def hebdo_cell(inspections: list[Inspection], th: Thresholds | None = None) -> DayCell:
    """RAP-04 : contrôle hebdomadaire de la semaine (mêmes états que le journalier)."""
    completed = [i for i in inspections if i.completed]
    if completed:
        return _closed_cell(completed, th, 0)
    if inspections:
        return DayCell(ORANGE, "Non clôturé", ["hebdo non clôturé"], inspections[-1].link)
    return DayCell(ROUGE, "Manquant", ["pas d'hebdo"])


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


def _elec_answers(items: list[dict], prefix: str) -> dict[str, tuple[str, str]]:
    states = {}
    for item in items:
        label = item.get("label") or ""
        state = response_state(item.get("response") or "")
        # « Non Concerné » (ou vide) : le parc n'a pas cette machine
        if is_elec(label, prefix) and state != INCONNU:
            states[game_name(label, prefix)] = (state, (item.get("comment") or "").strip())
    return states


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
        timeline = [_elec_answers(items_by_inspection[i.id], prefix) for i in history]
        site_states = {}
        for game, (state, comment) in timeline[-1].items():
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


def breakdown_counts(
    items_by_inspection: dict[str, list[dict]],
    hebdos: list[Inspection],
    prefix: str,
    start: date,
    end: date,
) -> dict[tuple[str, str], tuple[int, int]]:
    """RAP-07 : par (parc, machine), (nb de réponses « En Panne », nb de réponses).

    Seule la réponse « En Panne » compte comme panne (pas « En Service mais Maintenance »).
    """
    counts: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for insp in hebdos:
        if not insp.completed or not (start <= insp.day <= end):
            continue
        for game, (state, _) in _elec_answers(items_by_inspection.get(insp.id, []), prefix).items():
            if state == INCONNU:
                continue
            counts[(insp.site, game)][1] += 1
            counts[(insp.site, game)][0] += state == PANNE
    return {k: (c[0], c[1]) for k, c in counts.items()}


def sum_counts(
    counts: dict[tuple[str, str], tuple[int, int]],
    *,
    site: str | None = None,
    game: str | None = None,
) -> tuple[int, int]:
    """Agrège des comptes (pannes, réponses) sur un parc et/ou une machine."""
    p = n = 0
    for (s, g), (down, total) in counts.items():
        if (site is None or s == site) and (game is None or g == game):
            p += down
            n += total
    return p, n


def rate(pair: tuple[int, int]) -> float | None:
    return 100 * pair[0] / pair[1] if pair[1] else None


def quarter_start(day: date) -> date:
    return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


def previous_quarter_start(day: date) -> date:
    start = quarter_start(day)
    return quarter_start(start - timedelta(days=1))


def quarter_label(day: date) -> str:
    return f"T{(day.month - 1) // 3 + 1} {day.year}"


# ---------------------------------------------------------------------- actions
@dataclass
class ActionStats:
    creees: int = 0  # actions Mitti créées dans la semaine
    transferees: int = 0  # … passées « In progress » ou « Complete »
    rejetees: int = 0  # … passées « Can't do »
    ouvertes: int = 0  # actions Mitti encore « To do » (non transférées)
    a_traiter: int = 0  # lignes AC non cochées dans Smartsheet en fin de semaine
    anciennes: int = 0  # … dont signalées depuis plus de N jours
    traitees_semaine: int = 0  # lignes AC terminées (Fin) dans la semaine
    plus_ancienne: date | None = None


# ---------------------------------------------------------------------- priorité
@dataclass
class Priority:
    site: str
    score: float
    reasons: list[str]


def priority(
    site: str,
    stats: ActionStats,
    note_journaliers: float | None,
    note_hebdo: float | None,
) -> Priority:
    """RAP-08 : priorité de MAINTENANCE d'un parc, sur 100 (actions + note des contrôles).

    - Note des contrôles (40 pts) : (100 − note) × 2, la note étant la moyenne de la note
      moyenne des journaliers et de celle de l'hebdo de la semaine.
    - Nouvelles actions de la semaine non rejetées (20 pts) : 2 pts par action.
    - Stock d'actions à traiter (25 pts) : 0,5 pt par action.
    - Actions anciennes, plus de 30 jours (15 pts) : 0,5 pt par action.
    Les inspections manquantes ou incohérentes ne comptent pas ici.
    """
    reasons: list[str] = []
    notes = [n for n in (note_journaliers, note_hebdo) if n is not None]
    note = sum(notes) / len(notes) if notes else None
    score = 0.0
    if note is not None:
        score += min(40.0, max(0.0, (100 - note) * 2))
        detail = []
        if note_journaliers is not None:
            detail.append(f"journaliers {note_journaliers:.0f} %")
        if note_hebdo is not None:
            detail.append(f"hebdo {note_hebdo:.0f} %")
        reasons.append("note " + ", ".join(detail))
    nouvelles = stats.creees - stats.rejetees
    if nouvelles > 0:
        score += min(20.0, 2.0 * nouvelles)
        reasons.append(
            f"{nouvelles} nouvelle{'s' if nouvelles > 1 else ''} action"
            f"{'s' if nouvelles > 1 else ''}"
        )
    if stats.a_traiter:
        score += min(25.0, 0.5 * stats.a_traiter)
        txt = f"{stats.a_traiter} à traiter"
        if stats.anciennes:
            score += min(15.0, 0.5 * stats.anciennes)
            txt += f" dont {stats.anciennes} de plus de 30 j"
        reasons.append(txt)
    return Priority(site, round(score), reasons)


# ---------------------------------------------------------------------- tendances
@dataclass
class Kpi:
    label: str
    value: str
    current: float | None
    previous: float | None
    better: str | None = None  # "up", "down" ou None (neutre)
    unit: str = ""
    hint: str = ""

    @property
    def trend(self) -> str:
        """« up », « down » ou « flat » (évolution vs semaine précédente)."""
        if self.current is None or self.previous is None:
            return "none"
        if abs(self.current - self.previous) < 1e-9:
            return "flat"
        return "up" if self.current > self.previous else "down"

    @property
    def tone(self) -> str:
        """« good », « bad » ou « neutral »."""
        if self.better is None or self.trend in ("flat", "none"):
            return "neutral"
        return "good" if self.trend == self.better else "bad"

    @property
    def delta(self) -> str:
        if self.trend == "none":
            return "pas d'historique"
        diff = (self.current or 0) - (self.previous or 0)
        return f"{diff:+.0f}{' pts' if self.unit == '%' else ''} vs S-1"


# ---------------------------------------------------------------------- helpers
def pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.1f}".rstrip("0").rstrip(".") + " %"


def fmt_duration(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds} s"
    return f"{round(seconds / 60)} min"


def _dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
