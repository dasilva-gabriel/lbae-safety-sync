"""Ligne de commande Safety-Sync.

safety-sync sync [--dry-run]               # étapes 1 à 3 (tous les jours à 6h)
safety-sync transfert [--dry-run]          # étapes 1 et 2 seules
safety-sync terminees [--dry-run]          # étape 3 seule
safety-sync rapport [--semaine AAAA-MM-JJ] [--dry-run]   # rapport du lundi 9h
safety-sync diagnostic                     # connexions, trames, sites, feuilles Travaux

En cas d'erreur (hors --dry-run), une alerte est envoyée à SAFETYSYNC_ALERT_TO
(+ SAFETYSYNC_ALERT_CC).
"""

from __future__ import annotations

import argparse
import base64
import logging
import sys
import traceback
from collections import Counter
from datetime import date, datetime, timedelta
from html import escape

from safetysync.completion import run_completion
from safetysync.config import (
    get_mitti_settings,
    get_report_settings,
    get_smartsheet_settings,
    get_smtp_settings,
    get_sync_settings,
)
from safetysync.logs import setup_logging
from safetysync.mailer import SmtpMailer
from safetysync.mitti import MittiAPI
from safetysync.parcs import referentiel_from_sites
from safetysync.smartsheet import SmartsheetAPI
from safetysync.transfer import SheetCache, run_transfer

logger = logging.getLogger("safetysync")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="safety-sync")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_ in (
        ("sync", "Étapes 1 à 3 : transfert, mise à jour Mitti, synchro des terminées"),
        ("transfert", "Étapes 1 et 2 : transfert Mitti → Smartsheet"),
        ("terminees", "Étape 3 : synchro des actions terminées"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--dry-run", action="store_true", help="Aucune écriture, logs seulement")

    rap = sub.add_parser("rapport", help="Rapport hebdomadaire de maintenance")
    rap.add_argument(
        "--semaine",
        type=date.fromisoformat,
        help="Un jour de la semaine à couvrir (défaut : semaine dernière)",
    )
    rap.add_argument(
        "--dry-run",
        action="store_true",
        help="Écrit le HTML dans le dossier de sortie, sans envoyer l'email",
    )
    sub.add_parser("diagnostic", help="Vérifier les accès et lister trames, sites et feuilles")

    args = parser.parse_args(argv)
    log_path = setup_logging(verbose=args.verbose)
    logger.info("safety-sync %s (log : %s)", args.command, log_path)

    dry_run = getattr(args, "dry_run", False)
    try:
        if args.command == "diagnostic":
            return diagnostic()
        if args.command == "rapport":
            problems = rapport(args.semaine, dry_run)
        else:
            problems = sync(args.command, dry_run)
    except Exception as exc:
        logger.exception("Échec de safety-sync %s", args.command)
        _alert(args.command, [f"{type(exc).__name__}: {exc}"], traceback.format_exc(), dry_run)
        return 1
    if problems:
        _alert(args.command, problems, "", dry_run)
        return 2
    return 0


def _clients() -> tuple[MittiAPI, SmartsheetAPI]:
    return MittiAPI(get_mitti_settings()), SmartsheetAPI(get_smartsheet_settings())


def sync(command: str, dry_run: bool) -> list[str]:
    settings = get_sync_settings()
    problems: list[str] = []
    mitti, ss = _clients()
    with mitti, ss:
        sheets = SheetCache(ss, settings)
        problems += [f"Feuille Travaux en double : {d}" for d in sheets.duplicates]
        if command in ("sync", "transfert"):
            report = run_transfer(
                mitti, ss, settings, get_mitti_settings(), dry_run=dry_run, sheets=sheets
            )
            problems += [f"{o.unique_id} ({o.site}) : {o.detail}" for o in report.errors]
            _print_counts("Transfert", report.counts)
        if command in ("sync", "terminees"):
            completion = run_completion(mitti, sheets, settings, dry_run=dry_run)
            problems += completion.errors
            _print_counts("Actions terminées", completion.counts)
    return problems


def rapport(day: date | None, dry_run: bool) -> list[str]:
    from safetysync.report.collect import collect_weekly, previous_monday
    from safetysync.report.render import logo_bytes, render, subject

    settings = get_report_settings()
    today = datetime.now(settings.tz).date()
    monday = day - timedelta(days=day.weekday()) if day else previous_monday(today)
    mitti, ss = _clients()
    with mitti, ss:
        sheets = SheetCache(ss, get_sync_settings())
        report = collect_weekly(mitti, sheets, settings, get_sync_settings(), monday)
    if dry_run or not settings.report_to:
        # Fichier autonome : logo en data URI pour l'aperçu dans un navigateur.
        logo = "data:image/png;base64," + base64.b64encode(logo_bytes()).decode()
        settings.output_dir.mkdir(parents=True, exist_ok=True)
        path = settings.output_dir / f"rapport-{monday.isoformat()}.html"
        path.write_text(render(report, logo_src=logo), encoding="utf-8")
        print(f"Rapport écrit : {path}")
        if not dry_run:
            return [f"SAFETYSYNC_REPORT_TO est vide : rapport non envoyé (HTML écrit dans {path})."]
        return []
    SmtpMailer(get_smtp_settings()).send(
        settings.report_to,
        subject(report),
        render(report),
        cc=settings.report_cc,
        images={"logo": logo_bytes()},
    )
    print(f"Rapport envoyé à {', '.join(settings.report_to)}")
    return []


def diagnostic() -> int:
    settings = get_sync_settings()
    mitti, ss = _clients()
    with mitti, ss:
        nodes = list(mitti.feed_sites())
        report_settings = get_report_settings()
        ref = referentiel_from_sites(
            nodes,
            region_level=report_settings.region_level,
            excluded=report_settings.excluded_sites,
        )
        levels = Counter((n.get("meta_label") or "?") for n in nodes if not n.get("deleted"))
        print(f"Mitti : {len(ref.parcs)} parc(s), niveaux de l'arbre : {dict(levels)}")
        for region in ref.regions:
            print(f"  {region.nom} : {', '.join(region.parcs)}")
        since = datetime.now(settings.tz) - timedelta(days=14)
        templates: Counter = Counter()
        for raw in mitti.feed_inspections([], since):
            templates[(raw.get("template_id"), raw.get("template_name"))] += 1
        print("\nTrames utilisées ces 14 derniers jours (pour SAFETYSYNC_*_TEMPLATE_IDS) :")
        for (tid, name), n in templates.most_common():
            print(f"  {n:5d}  {name}  ->  {tid}")

        sheets = SheetCache(ss, settings)
        site_names = set(ref.site_names.values())
        print(f"\nSmartsheet : {len(sheets.by_site)} feuille(s) Travaux")
        for site in sorted(sheets.by_site):
            sheet = sheets.for_site(site)
            missing = [
                k for k in ("name", "details", "status_mgr", "comment_mgr") if not sheet.columns[k]
            ]
            notes = []
            if sheet.section_row_id is None:
                notes.append(f"section {settings.section_title} absente")
            if missing:
                notes.append("colonnes manquantes : " + ", ".join(missing))
            if site not in site_names:
                notes.append("aucun site Mitti de ce nom")
            print(f"  {site:10s} {sheet.name}  {'OK' if not notes else '⚠ ' + ' ; '.join(notes)}")
        no_sheet = sorted(site_names - set(sheets.by_site) - {""})
        if no_sheet:
            print("\nSites Mitti sans feuille Travaux : " + ", ".join(no_sheet))
        for dup in sheets.duplicates:
            print(f"Doublon : {dup}")
    return 0


def _print_counts(title: str, counts: Counter) -> None:
    print(f"{title} : " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "rien"))


def _alert(command: str, problems: list[str], details: str, dry_run: bool) -> None:
    settings = get_report_settings()
    if dry_run or not settings.alert_to:
        return
    try:
        items = "".join(f"<li>{escape(p)}</li>" for p in problems[:200])
        html = (
            f"<p>L'exécution de <b>safety-sync {escape(command)}</b> du "
            f"{datetime.now(settings.tz):%d/%m/%Y %H:%M} a rencontré "
            f"{len(problems)} problème(s) :</p><ul>{items}</ul>"
        )
        if details:
            html += f"<pre style='font-size:11px'>{escape(details[-5000:])}</pre>"
        html += "<p>Détails dans les logs du serveur.</p>"
        SmtpMailer(get_smtp_settings()).send(
            settings.alert_to, f"[Safety-Sync] Alerte : {command}", html, cc=settings.alert_cc
        )
    except Exception:
        logger.exception("Impossible d'envoyer l'alerte email")


if __name__ == "__main__":
    sys.exit(main())
