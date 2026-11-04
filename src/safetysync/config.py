"""Configuration chargée depuis les variables d'environnement (ou un fichier .env).

Listes et dictionnaires au format JSON dans l'env, ex. :
SAFETYSYNC_REPORT_TO='["dr1@exemple.fr", "dr2@exemple.fr"]'
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


class MittiSettings(BaseSettings):
    """API Mitti (ex-SafetyCulture). Le token doit être celui d'un compte de service
    pouvant voir et modifier toutes les actions de tous les sites."""

    model_config = SettingsConfigDict(env_prefix="MITTI_", **_ENV)

    api_token: SecretStr
    api_url: str = "https://api.mitti.com"
    # Lien vers une inspection dans l'application web ({inspection_id} est remplacé).
    inspection_url: str = "https://app.safetyculture.com/inspection/{inspection_id}"
    page_size: int = Field(default=100, ge=1, le=100)
    timeout_seconds: float = 60.0
    max_retries: int = 4


class SmartsheetSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMARTSHEET_", **_ENV)

    api_token: SecretStr
    api_url: str = "https://api.smartsheet.com/2.0"
    timeout_seconds: float = 60.0
    max_retries: int = 4


class SmtpSettings(BaseSettings):
    """Envoi des emails par SMTP (relais Brevo : smtp-relay.brevo.com:587, STARTTLS)."""

    model_config = SettingsConfigDict(env_prefix="", **_ENV)

    smtp_host: str = "smtp-relay.brevo.com"
    smtp_port: int = 587
    smtp_user: str
    smtp_password: SecretStr
    email_from: str
    email_from_name: str = "Safety-Sync"
    smtp_timeout_seconds: float = 30.0


class SyncSettings(BaseSettings):
    """Étapes 1 à 3 : transfert Mitti → Smartsheet et synchronisation des actions terminées."""

    model_config = SettingsConfigDict(env_prefix="SAFETYSYNC_", **_ENV)

    timezone: str = "Europe/Paris"
    # Étape 1 : ancienneté maximale des actions « To do » à transférer.
    lookback_days: int = Field(default=30, ge=1)
    # Réponses d'inspection qui justifient une ligne Smartsheet (insensible à la casse).
    valid_labels: list[str] = [
        "Premier Signalement",
        "En Panne",
        "En Service mais Maintenance",
        "Non OK",
        "Maintenance à effectuer",
    ]
    # Trames concernées (IDs Mitti). Vide = toutes les actions « To do ».
    template_ids: list[str] = []
    # Feuilles « Travaux … (CODE-PARC) » : le groupe « site » doit capturer le code du parc.
    sheet_pattern: str = r"(?i)\btravaux\b.*\((?P<site>[^)]+)\)\s*$"
    # Ligne de section sous laquelle les actions sont insérées.
    section_title: str = "MAINTENANCE JEUX"
    # Titres des colonnes Smartsheet (comparaison insensible à la casse, « commence par »).
    col_site: str = "SITE"
    col_name: str = "Nom de la tâche"
    col_details: str = "Détails"
    col_start: str = "Début"
    col_end: str = "Fin"
    col_status_mgr: str = "Statut Mgr"
    col_comment_mgr: str = "Commentaire Manager"
    # Colonnes facultatives : si absentes, l'info est ajoutée au Commentaire Manager.
    col_reporter: str = "Signalé par"
    col_report_link: str = "Rapport Mitti"
    # Étape 3 : ancienneté maximale des actions Mitti indexées pour la synchro des terminées.
    completion_horizon_days: int = Field(default=730, ge=1)
    # Pause entre deux écritures (limites de débit des API).
    write_pause_seconds: float = 0.2

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


class ReportSettings(BaseSettings):
    """Rapport hebdomadaire (lundi 9h)."""

    model_config = SettingsConfigDict(env_prefix="SAFETYSYNC_", **_ENV)

    timezone: str = "Europe/Paris"
    report_to: list[str] = []
    report_cc: list[str] = []
    # Alertes techniques (erreurs d'exécution) : destinataire principal + copies.
    alert_to: list[str] = []
    alert_cc: list[str] = []
    # IDs des trames Mitti (voir `safety-sync diagnostic`).
    journalier_template_ids: list[str] = []
    hebdo_template_ids: list[str] = []
    # Régions, responsables et parcs (TOML, voir parcs.example.toml).
    parcs_file: Path = Path("parcs.toml")
    # Jour considéré comme ouvert si au moins ce ratio de parcs a fait son journalier.
    opening_ratio: float = Field(default=0.2, ge=0, le=1)
    # Durée minimale crédible d'un contrôle journalier (secondes).
    min_duration_seconds: int = Field(default=300, ge=0)
    # Questions « jeux électroniques » de l'hebdo : préfixe du libellé.
    elec_prefix: str = "[Jeu Elec.]"
    # Historique des hebdos lu pour calculer depuis quand un jeu est en panne.
    elec_history_weeks: int = Field(default=8, ge=1)
    # Nombre de parcs affichés dans « Parcs prioritaires ».
    priority_top: int = Field(default=10, ge=1)
    # Action « à traiter » considérée comme ancienne au-delà de ce nombre de jours.
    old_action_days: int = Field(default=30, ge=1)
    # Dossier où `rapport --dry-run` écrit le HTML.
    output_dir: Path = Path("rapports")

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache
def get_mitti_settings() -> MittiSettings:
    return MittiSettings()  # type: ignore[call-arg]


@lru_cache
def get_smartsheet_settings() -> SmartsheetSettings:
    return SmartsheetSettings()  # type: ignore[call-arg]


@lru_cache
def get_smtp_settings() -> SmtpSettings:
    return SmtpSettings()  # type: ignore[call-arg]


@lru_cache
def get_sync_settings() -> SyncSettings:
    return SyncSettings()


@lru_cache
def get_report_settings() -> ReportSettings:
    return ReportSettings()
