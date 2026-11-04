import logging
from logging.handlers import TimedRotatingFileHandler
import os

# Configuration du dossier de logs
LOG_DIR = os.path.join(os.path.dirname(__file__), '../logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'safety_link.log')

# Configuration du logger
logger = logging.getLogger('SafetyLink')
logger.setLevel(logging.DEBUG)

# Gestion rotative des logs, 1 fichier par jour, conservation sur 20 jours
handler = TimedRotatingFileHandler(LOG_FILE, when="midnight", interval=1, backupCount=20)
formatter = logging.Formatter('%(asctime)s - %(levelname)-5s - %(module)s::%(funcName)s (%(lineno)d) ~ %(message)s')
handler.setFormatter(formatter)

logger.addHandler(handler)
