"""Journalisation : console + fichier rotatif (un fichier par jour, N fichiers max).

Fichiers : `<dir>/safety-sync.log` (jour courant) puis `safety-sync.log.AAAA-MM-JJ`.
La rotation se fait à minuit, ou au premier log d'un nouveau jour si le
programme ne tournait pas (date de dernière modification du fichier).
"""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class LogSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SAFETYSYNC_LOG_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    dir: Path = Path("logs")
    level: str = "INFO"
    # Nombre total de fichiers conservés, fichier du jour compris
    max_files: int = Field(default=10, ge=1)


def setup_logging(settings: LogSettings | None = None, *, verbose: bool = False) -> Path:
    """Configure le logger racine ; renvoie le chemin du fichier de log."""
    settings = settings or LogSettings()
    level = logging.DEBUG if verbose else getattr(logging, settings.level.upper(), logging.INFO)
    settings.dir.mkdir(parents=True, exist_ok=True)
    path = settings.dir / "safety-sync.log"

    file_handler = TimedRotatingFileHandler(
        path,
        when="midnight",
        backupCount=settings.max_files - 1,
        encoding="utf-8",
    )
    console = logging.StreamHandler()
    formatter = logging.Formatter(FORMAT)
    for handler in (file_handler, console):
        handler.setFormatter(formatter)

    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
        old.close()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(console)
    # httpx logue chaque requête en INFO : trop bavard hors mode verbeux
    logging.getLogger("httpx").setLevel(logging.DEBUG if verbose else logging.WARNING)
    return path
