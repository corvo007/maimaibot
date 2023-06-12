from typing import Tuple

import httpx

from database import chart_info, song_data_version, song_info
from log import logger

general_stat = {}


async def get_song_version() -> Tuple[str, str]:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = (await client.get("https://bucket-1256206908.cos.ap-shanghai.myqcloud.com/update.json")).json()
    except Exception as e:
        raise
    return resp["data_version"], resp["data_url"]


async def check_song_update() -> None:
    logger.info("checking update for song database")
    try:
        remote_version = (await get_song_version())[0]
        remote_data_url = (await get_song_version())[1]
        try:
            local_version = song_data_version.get_or_none().version
        except Exception as e:
            local_version = -1
    except Exception as e:
        logger.exception(e)
        logger.critical(
            f"Error <{e}> encountered while checking update for song database"
        )
        return
    if remote_version != local_version:
        logger.info("song database need update, updating...")
        await run_song_update(remote_data_url, remote_version)


async def run_song_update(data_url: str, new_version: str) -> None:
    songs_data = []
    charts_data = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = (await client.get(data_url)).json()
    except Exception as e:
        logger.exception(e)
        logger.critical(
            f"Error <{e}> encountered while checking update for song database"
        )
        return
    for song in resp:
        song_info_dict = {
            "song_id": song["id"],
            "artist": song["basic_info"]["artist"],
            "song_title": song["basic_info"]["title"],
            "bpm": song["basic_info"]["bpm"],
            "version": song["basic_info"]["from"],
            "genre": song["basic_info"]["genre"],
            "is_new": song["basic_info"]["is_new"],
            "type": 0 if song["type"] == "DX" else 1
        }
        
        songs_data.append(song_info_dict)
        for index, charts in enumerate(song["charts"]):
            charts_info_dict = {
                "song_id": song["id"],
                "level": index + 1,
                "chart_design": charts["charter"],
                "tap_note": charts["notes"][0],
                "hold_note": charts["notes"][1],
                "slide_note": charts["notes"][2],
                "touch_note": charts["notes"][3] if song["type"] == "DX" else 0,  # 仅DX谱有touch
                "break_note": charts["notes"][4] if song["type"] == "DX" else charts["notes"][3],
                "difficulty": song["ds"][index]
            }
            try:
                charts_info_dict["old_difficulty"] = song["old_ds"][index]
            except Exception as e:
                charts_info_dict["old_difficulty"] = -1
            
            charts_data.append(charts_info_dict)
    
    song_info.replace_many(songs_data).execute()
    chart_info.replace_many(charts_data).execute()
    song_data_version.replace(version=new_version).execute()


async def check_chart_stat_update():
    pass


async def run_chart_stat_update():
    pass
