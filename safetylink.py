from comp.globals import COMPLETE_ID
from comp.logger import logger
import comp.handler_safetyculture as hsc
import comp.handler_smartsheet as hss


def main():
    logger.debug("[START]")

    try:
        # Étape 1: Récupérer toutes les actions de SafetyCulture
        actions = hsc.get_actions()
        logger.info(f"{len(actions)} actions récupérées depuis SafetyCulture.")

        # Étape 2: Récupérer toutes les sheets disponibles dans SmartSheet
        sheets = hss.get_all_sheets()
        logger.info(f"{len(sheets)} sheets récupérées depuis SmartSheet.")

        # Étape 3: Traiter chaque action
        for act in actions:
            try:
                logger.debug(f"Traitement de l'action : {act.name}")

                # Chercher la feuille associée à l'action (Travaux % (self.site))
                associated_sheet = None
                for sheet_id, sheet_name in sheets:
                    if f"Travaux" in sheet_name and f"({act.site})" in sheet_name:
                        associated_sheet = (sheet_id, sheet_name)
                        logger.debug(f"Feuille associée trouvée : {sheet_name} (ID: {sheet_id})")
                        break

                if associated_sheet:
                    sheet_id, sheet_name = associated_sheet

                    try:
                        # Étape 1 : Récupérer les médias de l'action
                        media_list = hsc.get_action_media(act)
                        logger.info(f"{len(media_list)} média(s) récupéré(s) pour l'action '{act.name}'.")

                        # Étape 2 : Préparer les informations nécessaires pour la mise à jour
                        act.note = hsc.get_action_note(act)
                        logger.info(f"Note récupérée pour l'action '{act.name}': {act.note}")

                        # Étape 3 : Insérer une ligne dans la feuille associée
                        new_row_id = hss.insert_row_below_text(sheet_id, "MAINTENANCE JEUX")
                        if new_row_id:
                            logger.debug(
                                f"Ligne insérée sous 'MAINTENANCE JEUX', ID de la nouvelle ligne : {new_row_id}")

                            # Étape 4 : Mettre à jour la ligne avec les informations de l'action
                            hss.update_row_with_action(sheet_id, new_row_id, act)
                            logger.info(f"Action '{act.name}' insérée et mise à jour dans la feuille '{sheet_name}'.")

                            # Étape 5 : Attacher les médias à la ligne
                            if media_list:
                                hss.attach_media_to_row(sheet_id, new_row_id, media_list)
                            else:
                                logger.info(f"Aucun média à attacher pour l'action '{act.name}'.")

                            # Étape 6 : Mettre à jour l'état de l'action et ajouter une description
                            update_status = hsc.update_action_status(act.cache_id, COMPLETE_ID)
                            if update_status:
                                logger.info(f"Statut de l'action '{act.name}' mis à jour avec succès.")

                            description = f"[SAFETYLINK] Automatique ~ Transfert vers la feuille '{sheet_name}'"
                            update_desc = hsc.update_action_description(act.cache_id, description)
                            if update_desc:
                                logger.info(f"Description de l'action '{act.name}' mise à jour avec succès.")
                        else:
                            logger.warning(f"Impossible d'insérer une ligne pour l'action '{act.name}'.")

                    except Exception as e:
                        logger.error(f"Erreur lors du traitement de l'action '{act.name}': {e}")

                else:
                    logger.warning(
                        f"Aucune feuille associée trouvée pour le site '{act.site}' de l'action '{act.name}'.")

            except Exception as e:
                logger.error(f"Erreur lors du traitement de l'action '{act.name}' : {e}")

    except AssertionError as e:
        logger.error(f"Erreur dans le processus principal : {e}")

    logger.debug("[END]")


if __name__ == "__main__":
    logger.debug(" ")
    logger.debug("-" * 50)
    logger.debug("      SAFETYLINK - Processus de synchronisation SafetyCulture vers SmartSheet")
    logger.debug("-" * 50)
    main()
    logger.debug("-" * 50)
    logger.debug("      SAFETYLINK - Le processus est terminé.")
    logger.debug("-" * 50)
    logger.debug(" ")

