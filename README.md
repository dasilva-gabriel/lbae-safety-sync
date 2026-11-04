# Safety-Sync

Remonte les actions de maintenance créées dans **Mitti** (ex-SafetyCulture) lors des
contrôles Journalier et Hebdo vers les feuilles **Smartsheet** « Travaux … (GP-XXX) »,
synchronise les actions terminées dans les deux sens, et envoie chaque lundi un
**rapport de maintenance du réseau** (inspections, actions, jeux électroniques,
parcs prioritaires).

Règles détaillées : [`docs/fonctionnement.md`](docs/fonctionnement.md).

## Installation (Ubuntu 24.04)

```bash
git clone https://github.com/dasilva-gabriel/lbae-safety-sync.git
cd lbae-safety-sync
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env            # puis renseigner les tokens (comptes de service)
cp parcs.example.toml parcs.toml  # puis compléter régions et parcs
```

## Premier lancement

```bash
.venv/bin/safety-sync diagnostic          # accès, IDs des trames, sites, feuilles Travaux
.venv/bin/safety-sync sync --dry-run      # ce qui serait fait, sans rien modifier
.venv/bin/safety-sync rapport --dry-run   # écrit le HTML dans rapports/ sans l'envoyer
```

Reporter les IDs des trames Journalier et Hebdo affichés par `diagnostic` dans
`SAFETYSYNC_JOURNALIER_TEMPLATE_IDS` / `SAFETYSYNC_HEBDO_TEMPLATE_IDS`.

## Commandes

| Commande | Rôle |
|---|---|
| `safety-sync sync [--dry-run]` | Étapes 1 à 3 (chaque nuit à 6h) |
| `safety-sync transfert [--dry-run]` | Étapes 1 et 2 seules |
| `safety-sync terminees [--dry-run]` | Étape 3 seule |
| `safety-sync rapport [--semaine AAAA-MM-JJ] [--dry-run]` | Rapport (lundi 9h, semaine précédente par défaut) |
| `safety-sync diagnostic` | Vérifications de configuration |

Planification : `deploy/crontab.example`. Logs : `logs/safety-sync.log` (un fichier par jour).

## Développement

```bash
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```
