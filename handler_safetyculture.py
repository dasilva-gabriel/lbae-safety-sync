import requests
from comp.logger import logger
from comp.globals import SAFETYCULTURE_API_TOKEN, STATUS_TO_PUSH, COMPLETE_ID, CANT_DO
from comp.safetyaction import SafetyAction

HEADERS = {
    "Authorization": f"Bearer {SAFETYCULTURE_API_TOKEN}",
    "Content-Type": "application/json",
    "accept": "application/json"
}

# Cache pour stocker les détails des actions
action_cache = {}


def get_action_details(cache_id):
    """Récupère les détails d'une action et utilise un cache local pour éviter les appels redondants."""
    if cache_id in action_cache:
        return action_cache[cache_id]

    url = f"https://api.safetyculture.io/tasks/v1/actions/{cache_id}"
    try:
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        action_data = response.json()
        action_cache[cache_id] = action_data  # Ajouter au cache
        return action_data
    except requests.exceptions.RequestException as e:
        logger.error(f"Erreur lors de la récupération des détails pour l'action {cache_id}: {e}")
        return None


def get_valid_reference(action_details):
    """
    Recherche une référence valide dans une liste de références pour des labels précis.
    Les labels valides sont : "Premier Signalement", "Non OK", "Maintenance à effectuer", "En Panne" (insensibles à la casse).
    """
    valid_labels = {"premier signalement", "non ok", "maintenance à effectuer", "en panne", "en service mais maintenance"}

    references = action_details.get("action", {}).get("task", {}).get("references", [])
    for reference in references:
        responses = (
            reference.get("inspection_context", {})
            .get("list_answer", {})
            .get("responses", [])
        )
        for response in responses:
            label = response.get("label", "").strip().lower()
            if label in valid_labels:
                return reference

    logger.warning("Aucune référence valide trouvée avec les labels spécifiés.")
    return None


def check_list_answer(action):
    """Vérifie si une référence avec 'Premier Signalement' existe."""
    action_details = get_action_details(action.cache_id)
    if not action_details:
        return False

    valid_reference = get_valid_reference(action_details)
    if not valid_reference:
        logger.warning(f"L'action {action.cache_id} sera ignorée car aucune référence valide n'a été trouvée.")
        update_action_status(action.cache_id, CANT_DO)
        update_action_description(
            action.cache_id,
            "[SAFETYLINK] Attention, si vous souhaitez que cette action remonte en Smartsheet: "
            "Merci de ne pas créer d'action via cette réponse. "
            "Veuillez utiliser la réponse 'Premier Signalement' pour créer des actions. "
            "Pour les autres réponses, merci d'associer une action déjà créée ! (Excepté pour les 'Signalé dans le Journalier'"
        )
        return False
    return True


def get_action_note(action):
    """Récupère la note associée à la première référence valide."""
    action_details = get_action_details(action.cache_id)
    if not action_details:
        return "/"

    valid_reference = get_valid_reference(action_details)
    if valid_reference:
        return valid_reference.get("inspection_context", {}).get("inspection_item_attachments", {}).get("note", "/")
    return "/"


def get_action_media(action):
    """Récupère les médias associés à une action spécifique."""
    action_details = get_action_details(action.cache_id)
    if not action_details:
        return []

    all_media_urls = []
    valid_reference = get_valid_reference(action_details)
    if not valid_reference:
        return []

    task_media = (
        valid_reference.get("inspection_context", {})
        .get("inspection_item_attachments", {})
        .get("media", [])
    )

    media_count = 1  # Pour incrémenter les noms de fichiers
    for media in task_media:
        media['download_as_attachment_name'] = f"{action.action_uid}-{media_count}.jpg"
        media_url = "https://api.safetyculture.io/media/v1/download/" + media['id']
        del media["id"]
        try:
            response = requests.get(media_url, params=media, headers=HEADERS)
            response.raise_for_status()
            media_urls = response.json().get("url", "")
            all_media_urls.append({"url": media_urls, "filename": f"{action.action_uid}-{media_count}.jpg"})
            media_count += 1
        except requests.exceptions.RequestException as e:
            logger.error(f"Erreur lors de la récupération du média {media['filename']}: {e}")

    logger.info(f"Nombre de médias trouvés pour l'action {action.cache_id}: {len(all_media_urls)}")
    return all_media_urls


def update_action_status(cache_id, status_id):
    """Met à jour le statut de l'action identifiée par cache_id avec un statut donné."""
    url = f"https://api.safetyculture.io/tasks/v1/actions/{cache_id}/status"
    data = {
        "status_id": status_id
    }

    try:
        logger.info(f"Mise à jour de l'état de l'action {cache_id} avec le statut {status_id}.")
        response = requests.put(url, headers=HEADERS, json=data)
        response.raise_for_status()
        logger.info(f"Statut de l'action {cache_id} mis à jour avec succès.")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Erreur lors de la mise à jour du statut de l'action {cache_id} : {e}")
        return False


def update_action_description(task_id, description):
    """Met à jour la description d'une action spécifique."""
    url = f"https://api.safetyculture.io/tasks/v1/actions/{task_id}/description"
    data = {"description": description}

    try:
        logger.info(f"Mise à jour de la description de l'action {task_id}.")
        response = requests.put(url, headers=HEADERS, json=data)
        response.raise_for_status()
        logger.info(f"Description de l'action {task_id} mise à jour avec succès.")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Erreur lors de la mise à jour de la description de l'action {task_id} : {e}")
        return False


def get_actions():
    """Récupère les 100 dernières actions depuis SafetyCulture et les transforme en objets SafetyAction."""
    url = "https://api.safetyculture.io/tasks/v1/actions/list"
    data = {
        "sort_field": "CREATED_AT",
        "sort_direction": "DESC",
        "page_size": 100
    }

    try:
        logger.info("Envoi de la requête pour récupérer les dernières actions.")
        response = requests.post(url, headers=HEADERS, json=data)
        response.raise_for_status()
        actions_data = response.json()

        safety_actions = []

        if "actions" in actions_data:
            for action in actions_data["actions"]:
                task = action.get("task", {})
                name = task.get("title", "Sans titre")
                desc = task.get("inspection_item", {}).get("inspection_item_name", "Pas de description")
                site = task.get("site", {}).get("name", "Lieu non spécifié")
                action_uid = task.get("unique_id", "UID manquant")
                status = task.get("status", {}).get("key")
                cache_id = task.get("task_id")

                if status in STATUS_TO_PUSH:
                    safety_action = SafetyAction(
                        name=name, desc=desc, site=site, action_uid=action_uid, cache_id=cache_id
                    )

                    if not check_list_answer(safety_action):
                        continue

                    safety_actions.append(safety_action)
                    logger.debug(f"Action récupérée : {safety_action}")
                else:
                    logger.debug(f"Action {action_uid} non récupérée car état {status}.")
        else:
            logger.warning("Aucune action trouvée dans la réponse de l'API.")

        return safety_actions

    except requests.exceptions.RequestException as e:
        logger.error(f"Erreur lors de la récupération des actions : {e}")
        return []
