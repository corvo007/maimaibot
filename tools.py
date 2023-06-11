import httpx
from log import logger

VERSION_FILE = "https://bucket-1256206908.cos.ap-shanghai.myqcloud.com/update.json"
general_stat = {}


def check_update():
    try:
        resp = httpx.get("https://bucket-1256206908.cos.ap-shanghai.myqcloud.com/update.json").json()
    except Exception as e:
        logger.error("update failed")
        logger.exception(e)
        return False
    data_version = resp["data_version"]
