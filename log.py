import logging
import os
import sys

LOG_PATH = os.path.join(os.path.dirname(__file__), "log")

os.makedirs(LOG_PATH, exist_ok=True)
_error_log_file = os.path.join(LOG_PATH, 'error.log')
_critical_log_file = os.path.join(LOG_PATH, 'critical.log')

formatter = logging.Formatter('[%(asctime)s %(name)s] %(levelname)s: %(message)s')
default_handler = logging.StreamHandler(sys.stdout)
default_handler.setFormatter(formatter)
error_handler = logging.FileHandler(_error_log_file, encoding='utf8')
error_handler.setLevel(logging.ERROR)
error_handler.setFormatter(formatter)
critical_handler = logging.FileHandler(_critical_log_file, encoding='utf8')
critical_handler.setLevel(logging.CRITICAL)
critical_handler.setFormatter(formatter)

logger = logging.getLogger("maibot")
logger.addHandler(default_handler)
logger.addHandler(error_handler)
logger.addHandler(critical_handler)
logger.setLevel(logging.DEBUG)