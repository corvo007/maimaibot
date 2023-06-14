import asyncio
import decimal
import json
from typing import List, Literal, Optional, Tuple

import httpx
import numpy as np
from peewee import fn
from playhouse.shortcuts import model_to_dict

from database import chart_info, chart_stat, song_data_version, song_info
from log import logger

VERSION_FILE = "https://bucket-1256206908.cos.ap-shanghai.myqcloud.com/update.json"
STAT_API = "https://www.diving-fish.com/api/maimaidxprober/chart_stats"

general_stat = {}
new_song_id = []

new_song_id = [
    10146,
    10176,
    11349,
    11350,
    11351,
    11352,
    11353,
    11354,
    11357,
    11359,
    11360,
    11361,
    11362,
    11363,
    11364,
    11365,
    11366,
    11401,
    11402,
    11507,
    11508,
    11509,
]

song_rating_coefficient = [
    [0, 0, "d"],
    [50, 8, "c"],
    [60, 9.6, "b"],
    [70, 11.2, "bb"],
    [75, 12.0, "bbb"],
    [80, 13.6, "a"],
    [90, 15.2, "aa"],
    [94, 16.8, "aaa"],
    [97, 20, "s"],
    [98, 20.3, "sp"],
    [99, 20.8, "ss"],
    [99.5, 21.1, "ssp"],
    [99.9999, 21.4, "ssp"],
    [100, 21.6, "sss"],
    [100.4999, 22.2, "sss"],
    [100.5, 22.4, "sssp"],
]


async def get_song_version() -> Tuple[str, str]:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = (await client.get(VERSION_FILE)).json()
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
    global new_song_id
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
    new_song_id = []
    for song in resp:
        song_info_dict = {
            "song_id": int(song["id"]),
            "artist": song["basic_info"]["artist"],
            "song_title": song["basic_info"]["title"],
            "bpm": song["basic_info"]["bpm"],
            "version": song["basic_info"]["from"],
            "genre": song["basic_info"]["genre"],
            "is_new": song["basic_info"]["is_new"],
            "type": 0 if song["type"] == "DX" else 1,
        }
        if song["basic_info"]["is_new"]:
            new_song_id.append(song["id"])
        songs_data.append(song_info_dict)
        for index, charts in enumerate(song["charts"]):
            charts_info_dict = {
                "song_id": int(song["id"]),
                "level": index + 1,
                "chart_design": charts["charter"],
                "tap_note": charts["notes"][0],
                "hold_note": charts["notes"][1],
                "slide_note": charts["notes"][2],
                "touch_note": charts["notes"][3]
                if song["type"] == "DX"
                else 0,  # 仅DX谱有touch
                "break_note": charts["notes"][4]
                if song["type"] == "DX"
                else charts["notes"][3],
                "difficulty": song["ds"][index],
            }
            try:
                charts_info_dict["old_difficulty"] = song["old_ds"][index]
            except Exception as e:
                charts_info_dict["old_difficulty"] = -1

            charts_data.append(charts_info_dict)

    song_info.replace_many(songs_data).execute()
    chart_info.replace_many(charts_data).execute()
    song_data_version.replace(version=new_version).execute()


async def run_chart_stat_update() -> None:
    global general_stat
    chart_stats = []
    logger.info("updating chart statistics")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = (await client.get(STAT_API)).json()
    except Exception as e:
        logger.exception(e)
        logger.critical(
            f"Error <{e}> encountered while checking update for chart statistics"
        )
        return
    general_stat = resp["diff_data"]
    for k, v in resp["charts"].items():
        for index, chart in enumerate(v):
            if not chart:
                continue
            chart_stats.append(
                {
                    "song_id": int(k),
                    "level": index + 1,
                    "sample_num": chart["cnt"],
                    "fit_difficulty": decimal.Decimal(chart["fit_diff"]).quantize(
                        decimal.Decimal("0.00000")
                    ),
                    "avg_achievement": decimal.Decimal(chart["avg"]).quantize(
                        decimal.Decimal("0.00000")
                    ),
                    "avg_dxscore": decimal.Decimal(chart["avg_dx"]).quantize(
                        decimal.Decimal("0.00000")
                    ),
                    "std_dev": decimal.Decimal(chart["std_dev"]).quantize(
                        decimal.Decimal("0.00000")
                    ),
                    "achievement_dist": chart["dist"],
                    "fc_dist": chart["fc_dist"],
                }
            )
    chart_stat.replace_many(chart_stats).execute()


def separate_personal_data(personal_raw_data: List[dict]) -> Tuple[List, List]:
    personal_raw_data = personal_raw_data["records"]
    new_charts = filter(lambda x: x["song_id"] in new_song_id, personal_raw_data)
    old_charts = filter(lambda x: x["song_id"] not in new_song_id, personal_raw_data)
    return list(old_charts), list(new_charts)


async def record_player_data(personal_raw_data: List[dict]) -> None:
    # 后台任务
    pass


async def recommend_charts(
    personal_grades: List[dict],
    grade_type: Literal["new", "old"],
    preferences: Optional[Literal["aggressive", "balance", "conservative"]] = "balance",
    limit: int = 10,
) -> List[dict]:
    personal_grades.sort(key=lambda x: x["ra"], reverse=True)
    if grade_type == "new":
        charts_score = [int(x["ra"]) for x in personal_grades[:15]]
    else:
        charts_score = [int(x["ra"]) for x in personal_grades[:35]]
    median_score = np.median(charts_score)
    min_score = np.min(charts_score)
    if preferences == "balance":
        # min:SS (99.00%) max:SS+(99.50%)
        # max+min_score ~ min+median_score
        upper_difficulty = median_score * 100 / 99.00 / song_rating_coefficient[-6][1]
        lower_difficulty = (
            (min_score + 1) * 100 / 99.50 / song_rating_coefficient[-5][1]
        )
    elif preferences == "conservative":
        # min:SS+ Top(99.99%) max:SSS+(100.50%)
        upper_difficulty = median_score * 100 / 99.99 / song_rating_coefficient[-4][1]
        lower_difficulty = (
            (min_score + 1) * 100 / 100.50 / song_rating_coefficient[-1][1]
        )
    else:
        # min:S(97.00%) max:S+(98.00%)
        upper_difficulty = median_score * 100 / 97.00 / song_rating_coefficient[-8][1]
        lower_difficulty = (
            (min_score + 1) * 100 / 98.00 / song_rating_coefficient[-7][1]
        )

    upper_difficulty = float(upper_difficulty)
    lower_difficulty = float(lower_difficulty)

    if lower_difficulty > upper_difficulty:
        lower_difficulty, upper_difficulty = upper_difficulty, lower_difficulty

    base_condition = chart_info.difficulty.between(lower_difficulty, upper_difficulty)

    grade_condition = (
        (song_info.is_new == True)
        if grade_type == "new"
        else (song_info.is_new == False)
    )

    # 使用 IF 函数，当 like 和 dislike 之和小于5时设为 0.5，否则计算比例
    like_dislike_ratio = fn.IF(
        (chart_stat.like + chart_stat.dislike) < 5,
        0.5,
        chart_stat.like / (chart_stat.like + chart_stat.dislike),
    )

    # 构建排序计算公式
    order_expression = (
        chart_info.difficulty - chart_stat.fit_difficulty + like_dislike_ratio
    ) * chart_stat.weight

    query = (
        chart_info.select(chart_info, chart_stat, song_info)
        .join(
            chart_stat,
            on=(chart_info.song_id == chart_stat.song_id)
            & (chart_info.level == chart_stat.level),
        )
        .join(song_info, on=(chart_info.song_id == song_info.song_id))
        .where(base_condition & grade_condition)
        .order_by(order_expression.desc())
        .limit(limit)
    )

    # 查询结果转换为包含字典的列表
    result_list = []
    for record in query:
        chart_info_dict = model_to_dict(record)
        chart_stat_dict = model_to_dict(record.chart_stat)
        song_info_dict = model_to_dict(record.song_id)

        merged_dict = {**chart_info_dict, **chart_stat_dict, **song_info_dict}
        result_list.append(merged_dict)
    print([f'{x["song_title"]}({x["level"]},{x["difficulty"]})' for x in result_list])
    return result_list


with open("response.json") as f:
    c = json.load(f)

asyncio.run(
    recommend_charts((separate_personal_data(c))[0], "old", "conservative"), debug=True
)
