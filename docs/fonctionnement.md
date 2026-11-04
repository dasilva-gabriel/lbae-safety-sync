# Fonctionnement de Safety-Sync

Toute nouvelle règle communiquée doit être ajoutée ici (identifiants `SYNC-xx` pour la
synchronisation, `RAP-xx` pour le rapport) et implémentée dans `src/safetysync/`.

## Synchronisation nocturne (`safety-sync sync`, 6h00)

### Étape 1 — Transfert Mitti → Smartsheet (`transfer.py`)

| Règle | Description |
|---|---|
| SYNC-01 | Lecture de **toutes** les actions Mitti « To do » créées depuis 30 jours (`SAFETYSYNC_LOOKBACK_DAYS`). Toutes les pages sont lues (`next_page_token`) : les anciens scripts ne lisaient que les 100 dernières. |
| SYNC-02 | Seule une action liée à une réponse **Premier Signalement** est transférée (ou, dans l'hebdo, **En Panne**, **En Service mais Maintenance**, **Non OK** ; liste `SAFETYSYNC_VALID_LABELS`). Ces réponses imposent photo et commentaire dans les trames, donc la ligne Smartsheet n'est jamais vide. |
| SYNC-03 | Feuille cible : la feuille dont le nom contient « Travaux » et se termine par `(CODE)`, où CODE est **identique** au nom du site Mitti (ex. `GP-LMA`). |
| SYNC-04 | La ligne est insérée **juste sous** la ligne « MAINTENANCE JEUX » (comme les anciens scripts). |
| SYNC-05 | Contenu de la ligne : SITE = code parc ; Nom de la tâche = titre de l'action ; Détails = `[AC-xxx] ~ question de l'inspection` ; Début = date du signalement ; Commentaire Manager = note de l'inspection + « Signalé par » (créateur de l'action) + lien vers le rapport Mitti. Si la feuille a des colonnes « Signalé par » / « Rapport Mitti », elles sont remplies à la place. Les photos de la question d'inspection sont jointes à la ligne. |
| SYNC-06 | Anti-doublon : si `[AC-xxx]` est déjà présent dans la colonne Détails de la feuille, aucune ligne n'est ajoutée. |

### Étape 2 — Mise à jour des actions Mitti (`transfer.py`)

| Règle | Description |
|---|---|
| SYNC-07 | Action transférée (ou déjà présente) : statut **In progress** + commentaire `[SAFETY-SYNC] Transférée le JJ/MM/AAAA dans la feuille …`. La description saisie par le parc n'est plus écrasée : le suivi passe par les commentaires. |
| SYNC-08 | Action non transférable : statut **Can't do** + commentaire expliquant pourquoi : pas de Premier Signalement (ex. action créée sur « Signalé dans l'Hebdo » au lieu de lier l'action existante), pas de site, pas de feuille Travaux pour le parc, ou pas de ligne « MAINTENANCE JEUX ». |
| SYNC-09 | Erreur technique (API indisponible…) : l'action reste « To do » et sera retentée la nuit suivante. Une alerte email est envoyée. |

### Étape 3 — Actions terminées (`completion.py`)

| Règle | Description |
|---|---|
| SYNC-10 | « Statut Mgr » coché dans Smartsheet et action Mitti « To do » / « In progress » → **Complete** dans Mitti + commentaire. |
| SYNC-11 | Action **Complete** dans Mitti et « Statut Mgr » non coché → case cochée, « Fin » = date de clôture Mitti (si vide), mention ajoutée au Commentaire Manager. |
| SYNC-12 | Pas de réouverture automatique (case décochée, action rouverte). Les actions « Can't do » ne sont jamais modifiées. |

Les actions Mitti des 730 derniers jours sont indexées (`SAFETYSYNC_COMPLETION_HORIZON_DAYS`).

## Rapport hebdomadaire (`safety-sync rapport`, lundi 9h00)

Couvre la semaine précédente (lundi → dimanche). Envoyé par SMTP (relais Brevo, variables `SMTP_*` et `EMAIL_FROM`) à `SAFETYSYNC_REPORT_TO`
(+ `SAFETYSYNC_REPORT_CC`). Régions et parcs : `parcs.toml`.

| Règle | Description |
|---|---|
| RAP-01 | Jour d'une inspection : « Effectué le » (`conducted_on`), sinon la date de début. Le parc est le site Mitti de l'inspection. |
| RAP-02 | Un jour est **ouvert** si au moins 20 % des parcs (`SAFETYSYNC_OPENING_RATIO`) ont fait leur journalier ce jour-là. Sinon les parcs sans journalier sont en **gris** (fermé). |
| RAP-03 | Cellule journalier : **rouge** = pas de journalier un jour ouvert ; **jaune** = inspection non terminée, durée < 300 s (`SAFETYSYNC_MIN_DURATION_SECONDS`, « Durée non cohérente »), plusieurs journaliers le même jour avec des scores différents (« Score non cohérent (x % à y %) ») ou saisie commencée après le jour contrôlé (« Saisie a posteriori ») ; **bleu** = anticipée (commencée avant le jour contrôlé) ; **vert** = OK. Le texte est le score, avec un lien vers le rapport Mitti. |
| RAP-04 | Sans ID de trame configuré, les cellules sont grises « Non configuré » et ne pénalisent pas le parc. Colonne Hebdo : vert = au moins un hebdo terminé dans la semaine ; jaune = commencé mais pas terminé ; rouge = manquant. |
| RAP-05 | Actions par parc. Mitti (actions créées dans la semaine) : **créées**, **transférées** (In progress / Complete), **rejetées** (Can't do). **Non transférées** : actions encore « To do ». Smartsheet : **à traiter** (lignes `[AC-xxx]` non cochées), dont **anciennes** (signalées depuis plus de 30 jours, `SAFETYSYNC_OLD_ACTION_DAYS`), date de la plus ancienne, **traitées** (cochées avec Fin dans la semaine). |
| RAP-06 | Jeux électroniques : questions de l'hebdo commençant par `[Jeu Elec.]`, d'après le **dernier hebdo terminé** du parc. OK / Maintenance (« En Service mais Maintenance ») / En panne, avec le nombre d'hebdos consécutifs en panne (historique de 8 semaines, `SAFETYSYNC_ELEC_HISTORY_WEEKS`). |
| RAP-07 | Synthèse réseau : journaliers réalisés / attendus (jours ouverts), hebdos réalisés, jeux en panne / à maintenir, totaux d'actions. |
| RAP-08 | Score de priorité d'un parc (les 10 plus élevés sont affichés, `SAFETYSYNC_PRIORITY_TOP`) : jeu en panne +5 (+1 par semaine de panne supplémentaire, max +4) ; jeu à maintenir +1 ; hebdo manquant +4 (incomplet +2) ; journalier manquant +2 ; journalier jaune +1 ; action à traiter +0,5 (+0,5 si ancienne), part « actions » plafonnée à 8 points ; action Mitti non transférée +0,5. |

## Alertes

En cas d'erreur d'exécution ou d'actions en erreur, un email est envoyé à
`SAFETYSYNC_ALERT_TO` avec copie à `SAFETYSYNC_ALERT_CC`. Rien n'est envoyé en `--dry-run`.

## Points à vérifier au premier lancement

- `MITTI_INSPECTION_URL` : format exact du lien vers une inspection dans l'application web.
- Filtres `task_filters` de l'API (statut, date de création) : ils sont aussi appliqués côté
  client par sécurité. Vérifier dans les logs le nombre de pages lues.
- Format de date des réponses du feed `/feed/inspections` (`conducted_on`, `duration`).
