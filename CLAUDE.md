# Safety-Sync — notes pour Claude

Safety-Sync transfère les actions Mitti (ex-SafetyCulture) vers les feuilles Smartsheet
« Travaux … (GP-XXX) », synchronise les actions terminées et envoie le rapport
hebdomadaire de maintenance par email (Brevo). Voir `README.md`.

- **Règles** : `docs/fonctionnement.md` (`SYNC-xx` synchronisation, `RAP-xx` rapport).
  Toute nouvelle règle communiquée par l'utilisateur doit y être ajoutée.
- API Mitti : https://developer.mitti.com/reference (hôte `api.mitti.com`). API Smartsheet :
  https://developers.smartsheet.com (plus d'`includeAll` depuis juin 2026 : paginer).
  Vérifier les champs dans la doc plutôt que de les deviner.
- Code et docs en français, conventions de `lbae-sherlock`. Checks : `.venv/bin/pytest` et
  `.venv/bin/ruff check . && .venv/bin/ruff format --check .`
- Ne jamais afficher ni committer `.env` ni `parcs.toml`.
- Commits au nom de l'utilisateur : Gabriel DA SILVA <77939562+dasilva-gabriel@users.noreply.github.com>.
- Planification : crontab (sync tous les jours 6h00, rapport le lundi 9h00).

## État du projet (04/11/2026)

- Version 1 écrite et testée avec des API simulées. Reste à valider sur données réelles :
  `diagnostic`, puis `sync --dry-run` et `rapport --dry-run` sur le serveur.
