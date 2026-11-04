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
(+ `SAFETYSYNC_REPORT_CC`).

**Régions et parcs** : lus dans Mitti (data feed des sites, arbre complet). Un parc est un site
de niveau « location » ; sa région est son ancêtre de niveau « region »
(`SAFETYSYNC_REGION_LEVEL`), à défaut son parent direct, sinon « Sans région ». Les sites
supprimés sont ignorés ; `SAFETYSYNC_EXCLUDED_SITES` masque des sites (tests, parcs fermés).
Un parc vu dans les actions ou inspections mais absent de l'arbre est listé dans
« Autres parcs (hors arbre Mitti) ».

| Règle | Description |
|---|---|
| RAP-01 | Jour d'une inspection : « Effectué le » (`conducted_on`), sinon la date de début. Le parc est le site Mitti de l'inspection. |
| RAP-02 | Un jour est **ouvert** si au moins 20 % des parcs (`SAFETYSYNC_OPENING_RATIO`) ont fait leur journalier ce jour-là. Sinon les parcs sans journalier sont en **gris** (fermé). |
| RAP-03 | Contrôle **suspect** (jaune, « ! ») si l'un de ces signaux est présent : **trop rapide** (durée sous max(300 s, 40 % de la durée médiane du réseau sur la semaine) ; pour l'hebdo max(900 s, 40 % de la médiane des hebdos) ; `SAFETYSYNC_MIN_DURATION_SECONDS`, `SAFETYSYNC_MIN_HEBDO_DURATION_SECONDS`, `SAFETYSYNC_SUSPICIOUS_DURATION_RATIO`) ; **heure inhabituelle** (commencé avant 6h ou après 23h, `SAFETYSYNC_USUAL_HOUR_START/END`) ; **saisi après coup** (commencé après le jour contrôlé) ; **plusieurs scores** différents le même jour ; **100 % malgré des problèmes ouverts** (jeux en panne au dernier hebdo, ou actions signalées depuis moins de 14 jours et non terminées, `SAFETYSYNC_PERFECT_SCORE_CHECK`, `SAFETYSYNC_RECENT_ISSUE_DAYS`) ; ou **non terminé**. Sinon : **rouge** (✗) = pas de journalier un jour ouvert ; **bleu** (A) = anticipé (commencé avant le jour contrôlé) ; **vert** (✓) = OK. |
| RAP-04 | Hebdo : vert = au moins un hebdo terminé dans la semaine ; jaune = suspect ou non terminé ; rouge = manquant. Sans ID de trame configuré, les cellules sont grises « Non configuré » et ne pénalisent pas le parc. |
| RAP-05 | Actions par parc. Mitti (actions créées dans la semaine) : **créées**, **transférées** (In progress / Complete), **rejetées** (Can't do). Smartsheet : **à traiter** = lignes `[AC-xxx]` non cochées en fin de semaine (une ligne cochée avec une Fin postérieure est encore ouverte à cette date), dont **+30 j** (`SAFETYSYNC_OLD_ACTION_DAYS`), date de la plus ancienne, **traitées** (Fin dans la semaine). Bandeau d'alerte si plus de 30 % des actions créées sont rejetées. |
| RAP-06 | Jeux électroniques : questions de l'hebdo contenant `[Jeu Elec.]`, d'après le **dernier hebdo terminé** du parc. OK / Maintenance (« En Service mais Maintenance ») / En panne, avec le nombre d'hebdos consécutifs en panne. |
| RAP-07 | **Taux de panne par machine** = réponses « En Panne » / réponses à la question, sur les hebdos terminés de tous les parcs, pour le trimestre précédent et le trimestre en cours (jusqu'au dimanche du rapport). |
| RAP-08 | Score de priorité d'un parc (les 5 plus élevés sont affichés) : jeu en panne +5 (+1 par semaine de panne supplémentaire, max +4) ; jeu à maintenir +1 ; hebdo manquant +4 (suspect +2) ; journalier manquant +2 ; journalier suspect +1,5 ; action à traiter +0,5 (+0,5 si ancienne), part « actions » plafonnée à 8 points ; action Mitti non transférée +0,5. |
| RAP-09 | **État du réseau** : journaliers faits (% des jours ouverts), hebdos faits, contrôles suspects, jeux en panne, actions créées, taux de rejet, stock à traiter, actions traitées ; chacun comparé à la semaine précédente (▲/▼, vert si l'évolution est favorable, rouge sinon). |
| RAP-10 | Ordre du mail : État du réseau → Top 5 des parcs prioritaires (`SAFETYSYNC_PRIORITY_TOP`) → Inspections de la semaine (tableau compact par région) → Focus actions → Focus jeux électroniques. HTML en tableaux, compatible Outlook classique, logo LBAE intégré. |

## Alertes

En cas d'erreur d'exécution ou d'actions en erreur, un email est envoyé à
`SAFETYSYNC_ALERT_TO` avec copie à `SAFETYSYNC_ALERT_CC`. Rien n'est envoyé en `--dry-run`.

## Points à vérifier au premier lancement

- `MITTI_INSPECTION_URL` : format exact du lien vers une inspection dans l'application web.
- Filtres `task_filters` de l'API (statut, date de création) : ils sont aussi appliqués côté
  client par sécurité. Vérifier dans les logs le nombre de pages lues.
- Format de date des réponses du feed `/feed/inspections` (`conducted_on`, `duration`).
