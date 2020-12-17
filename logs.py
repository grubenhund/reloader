from pythonjsonlogger import jsonlogger
import logging


logger = logging.getLogger("reloader")
logger.setLevel('INFO')
json_handler = logging.StreamHandler()
formatter = jsonlogger.JsonFormatter(
    fmt='%(asctime)s %(levelname)s %(name)s %(message)s'
)
json_handler.setFormatter(formatter)
logger.addHandler(json_handler)
