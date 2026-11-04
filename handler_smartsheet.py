import smartsheet
import os
import requests
from comp.logger import logger
from comp.globals import SMARTSHEET_API_TOKEN, TEMP_DIR, COLUMN_NAME, COLUMN_DESC, COLUMN_SITE, COLUMN_COMMENT

smartsheet_client = smartsheet.Smartsheet(SMARTSHEET_API_TOKEN)
smartsheet_client.errors_as_exceptions(True)

def get_all_sheets():
    """
    Récupère toutes les sheets de l'utilisateur avec leurs ID et noms.
    """
    try:
        logger.info("Récupération de toutes les sheets disponibles.")
        sheets = smartsheet_client.Sheets.list_sheets(include_all=True)
        sheet_info = [(sheet.id, sheet.name) for sheet in sheets.data]
        logger.info(f"{len(sheet_info)} sheets récupérées.")
        return sheet_info
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des sheets : {e}")
        return []


def insert_row_below_text(sheet_id, search_text):
    """
    Insère une ligne vide en dessous de la ligne ayant une cellule contenant 'search_text' dans la feuille donnée.
    Retourne l'ID de la nouvelle ligne insérée.
    """
    try:
        logger.info(f"Insertion d'une ligne en dessous de celle contenant '{search_text}' dans la feuille {sheet_id}.")

        # Récupérer la feuille complète
        sheet = smartsheet_client.Sheets.get_sheet(sheet_id)

        # Recherche de la ligne contenant 'search_text'
        for row in sheet.rows:
            for cell in row.cells:
                if cell.value and search_text.lower() in str(cell.value).lower():
                    logger.info(f"Texte trouvé dans la ligne ID: {row.id}. Insertion de la nouvelle ligne.")

                    # Créer une nouvelle ligne vide
                    new_row = smartsheet.models.Row()
                    new_row.to_bottom = False  # La nouvelle ligne ne va pas en bas
                    new_row.sibling_id = row.id  # Insère la ligne juste en dessous de la ligne trouvée

                    response = smartsheet_client.Sheets.add_rows(sheet_id, [new_row])
                    new_row_id = response.result[0].id
                    logger.info(f"Nouvelle ligne insérée avec succès. ID de la ligne: {new_row_id}.")
                    return new_row_id

        logger.warning(f"Aucune ligne contenant '{search_text}' trouvée.")
        return None

    except Exception as e:
        logger.error(f"Erreur lors de l'insertion de la ligne : {e}")
        return None


def update_row_with_action(sheet_id, row_id, action):
    """
    Met à jour la ligne avec l'ID 'row_id' dans la feuille 'sheet_id'.
    Insère dans la colonne qui contient 'SITE' le `action.site`,
    dans la colonne contenant 'Nom de la tâche' le `action.name`,
    dans la colonne contenant 'Détails' le `[action.action_uid] ~ action.desc`,
    et dans la colonne contenant 'Commentaire Manager' le `action.note`.
    Si une colonne est manquante, cela est logué mais la mise à jour continue pour les colonnes disponibles.
    """
    try:
        logger.info(f"Mise à jour de la ligne ID {row_id} avec les informations de l'action.")

        # Récupérer la feuille pour obtenir les colonnes
        sheet = smartsheet_client.Sheets.get_sheet(sheet_id)

        # Trouver les ID des colonnes en fonction du nom des colonnes
        site_column_id = None
        name_column_id = None
        desc_column_id = None
        comment_column_id = None

        for column in sheet.columns:
            column_name_lower = column.title.lower()

            if COLUMN_SITE.lower() in column_name_lower:
                site_column_id = column.id
            elif COLUMN_NAME.lower() in column_name_lower:
                name_column_id = column.id
            elif COLUMN_DESC.lower() in column_name_lower:
                desc_column_id = column.id
            elif COLUMN_COMMENT.lower() in column_name_lower:
                comment_column_id = column.id

        # Création des cellules mises à jour
        updated_row = smartsheet.models.Row()
        updated_row.id = row_id

        # Mise à jour de la colonne 'SITE'
        if site_column_id:
            cell_site = smartsheet.models.Cell()
            cell_site.column_id = site_column_id
            cell_site.value = action.site
            updated_row.cells.append(cell_site)
        else:
            logger.warning(f"Colonne 'SITE' non trouvée dans la feuille {sheet.name}.")

        # Mise à jour de la colonne 'Nom de la tâche'
        if name_column_id:
            cell_name = smartsheet.models.Cell()
            cell_name.column_id = name_column_id
            cell_name.value = action.name
            updated_row.cells.append(cell_name)
        else:
            logger.warning(f"Colonne 'Nom de la tâche' non trouvée dans la feuille {sheet.name}.")

        # Mise à jour de la colonne 'Détails'
        if desc_column_id:
            cell_action = smartsheet.models.Cell()
            cell_action.column_id = desc_column_id
            cell_action.value = f"[{action.action_uid}] ~ {action.desc}"
            updated_row.cells.append(cell_action)
        else:
            logger.warning(f"Colonne 'Détails' non trouvée dans la feuille {sheet.name}.")

        # Mise à jour de la colonne 'Commentaire Manager'
        if comment_column_id:
            cell_comment = smartsheet.models.Cell()
            cell_comment.column_id = comment_column_id
            cell_comment.value = action.note
            updated_row.cells.append(cell_comment)
        else:
            logger.warning(f"Colonne 'Commentaire Manager' non trouvée dans la feuille {sheet.name}.")

        # Si aucune colonne n'est disponible, ne pas envoyer la requête d'update
        if not updated_row.cells:
            logger.error(f"Aucune colonne valide pour mettre à jour la ligne {row_id} dans la feuille {sheet.name}.")
            return None

        # Mise à jour de la ligne dans Smartsheet
        response = smartsheet_client.Sheets.update_rows(sheet_id, [updated_row])
        logger.info(f"Ligne mise à jour avec succès. ID de la ligne : {response.result[0].id}.")
        return response.result[0].id

    except Exception as e:
        logger.error(f"Erreur lors de la mise à jour de la ligne dans SmartSheet : {e}")
        return None

def attach_media_to_row(sheet_id, row_id, media_list):
    """
    Attache les médias téléchargés à une ligne spécifique dans SmartSheet.
    Les médias sont téléchargés temporairement, attachés à la ligne, puis supprimés.
    """
    try:
        for media in media_list:
            local_path = download_image(media)
            if local_path:
                # Ajout de l'attachement à la ligne SmartSheet
                attachment = smartsheet_client.Attachments.attach_file_to_row(
                    sheet_id,
                    row_id,
                    (media['filename'], open(local_path, 'rb'), 'image/jpeg')
                )
                if attachment.message == 'SUCCESS':
                    logger.info(f"Attachement ajouté avec succès pour la ligne ID: {row_id}, fichier: {media['filename']}")
                else:
                    logger.warning(f"Échec de l'ajout de l'attachement pour la ligne ID: {row_id}")

                # Suppression du fichier local après l'attachement
                os.remove(local_path)
                logger.info(f"Fichier temporaire supprimé: {local_path}")
            else:
                logger.warning(f"Échec du téléchargement du média : {media['filename']}")

    except Exception as e:
        logger.error(f"Erreur lors de l'attachement des médias pour la ligne {row_id} : {e}")

def download_image(media):
    """
    Télécharge un fichier image temporairement à partir de l'URL du média.
    Retourne le chemin local du fichier téléchargé.
    """
    try:
        response = requests.get(media['url'], stream=True)
        response.raise_for_status()
        local_path = os.path.join(TEMP_DIR, media['filename'])
        with open(local_path, 'wb') as out_file:
            for chunk in response.iter_content(chunk_size=8192):
                out_file.write(chunk)
        logger.info(f"Image téléchargée: {local_path}")
        return local_path
    except requests.exceptions.RequestException as e:
        logger.error(f"Erreur lors du téléchargement de l'image: {e}")
        return None