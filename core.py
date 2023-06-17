import asyncio
import decimal
import json
import time
from typing import List, Tuple

import httpx
import numpy as np
from peewee import JOIN, fn
from playhouse.shortcuts import model_to_dict
from pydantic import ValidationError

from database import (
    chart_blacklist,
    chart_info,
    chart_record,
    chart_stat,
    rating_record,
    song_data_version,
    song_info,
)
from exception import ParameterError
from log import logger
from model import player_preferences

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


async def record_player_data(personal_raw_data: List[dict], player_id: str) -> None:
    # 后台任务
    charts_list = []
    for charts in personal_raw_data["records"]:
        charts_list.append(
            {
                "player_id": player_id,
                "song_id": charts["song_id"],
                "level": charts["level_index"] + 1,
                "type": 1 if charts["type"] == "SD" else 0,
                "achievement": charts["achievements"],
                "rating": charts["ra"],
                "dxscore": charts["dxScore"],
                "fc_status": charts["fc"],
                "fs_status": charts["fs"],
            }
        )
    old_charts, new_charts = separate_personal_data(personal_raw_data)
    old_charts.sort(key=lambda x: x["ra"], reverse=True)
    new_charts.sort(key=lambda x: x["ra"], reverse=True)
    old_rating = 0
    new_rating = 0
    for i in old_charts[:35]:
        old_rating += i["ra"]
    for i in new_charts[:15]:
        new_rating += i["ra"]
    rating_record.replace(
        {
            "player_id": player_id,
            "old_song_rating": old_rating,
            "new_song_rating": new_rating,
        }
    ).execute()
    chart_record.replace_many(charts_list).execute()


async def recommend_charts(
    personal_raw_data: dict,
    preferences: dict = None,
    limit: int = 10,
) -> dict:
    time1 = time.time()
    messages_list = []
    if preferences is None:
        preferences = dict()
    try:
        preferences = player_preferences.parse_obj(preferences)
    except ValidationError as e:
        raise ParameterError(e)
    player_id = personal_raw_data["username"]
    personal_raw_data = personal_raw_data["records"]
    personal_raw_data.sort(key=lambda x: x["ra"], reverse=True)

    new_charts = list(filter(lambda x: x["song_id"] in new_song_id, personal_raw_data))
    old_charts = list(
        filter(lambda x: x["song_id"] not in new_song_id, personal_raw_data)
    )

    charts_score_new = [int(x["ra"]) for x in new_charts[:15]]
    charts_score_old = [int(x["ra"]) for x in old_charts[:35]]
    filtered_song_ids = []

    for score in personal_raw_data:
        if score["achievements"] >= 100.5000:
            filtered_song_ids.append(score["song_id"])
            continue
        if preferences.exclude_played and score["achievements"] >= 94:
            filtered_song_ids.append(score["song_id"])

    personal_grades_dict = {}
    for chart in personal_raw_data:
        personal_grades_dict[chart["song_id"]] = chart

    def _query_charts(is_new: bool, charts_score: list, filtered_song_ids: list):
        median_score = np.median(charts_score)
        min_score = np.min(charts_score)
        if preferences.recommend_preferences == "balance":
            # min:SS (99.00%) max:SS+(99.50%)
            # max+min_score ~ min+median_score
            upper_difficulty = (
                median_score * 100 / 99.00 / song_rating_coefficient[-6][1]
            )
            lower_difficulty = (
                (min_score + 1) * 100 / 99.50 / song_rating_coefficient[-5][1]
            )
            minium_achievement = 99.0000
        elif preferences.recommend_preferences == "conservative":
            # min:SS+ Top(99.99%) max:SSS+(100.50%)
            upper_difficulty = (
                median_score * 100 / 99.99 / song_rating_coefficient[-4][1]
            )
            lower_difficulty = (
                (min_score + 1) * 100 / 100.50 / song_rating_coefficient[-1][1]
            )
            minium_achievement = 100.0000
        else:
            # min:S(97.00%) max:S+(98.00%)
            upper_difficulty = (
                median_score * 100 / 97.00 / song_rating_coefficient[-8][1]
            )
            lower_difficulty = (
                (min_score + 1) * 100 / 98.00 / song_rating_coefficient[-7][1]
            )
            minium_achievement = 97.0000

        if lower_difficulty > upper_difficulty:
            lower_difficulty, upper_difficulty = upper_difficulty, lower_difficulty

        upper_difficulty = float(upper_difficulty)
        lower_difficulty = float(lower_difficulty)

        base_condition = chart_info.difficulty.between(
            lower_difficulty, upper_difficulty
        )

        grade_condition = (
            (song_info.is_new == True) if is_new else (song_info.is_new == False)
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
            .join(
                chart_blacklist,
                on=(
                    (chart_info.song_id == chart_blacklist.song_id)
                    & (chart_info.level == chart_blacklist.level)
                    & (chart_blacklist.player_id == player_id)
                ),
                join_type=JOIN.LEFT_OUTER,
            )
            .where(
                (base_condition & grade_condition)
                & (chart_blacklist.player_id.is_null())
                & (~(chart_info.song_id << filtered_song_ids))
            )
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

            if merged_dict["song_id"] in personal_grades_dict:
                if (
                    merged_dict["level"] - 1
                    != personal_grades_dict[merged_dict["song_id"]]["level_index"]
                ):
                    merged_dict["achievement"] = 0
                else:
                    merged_dict["achievement"] = personal_grades_dict[
                        merged_dict["song_id"]
                    ]["achievements"]
            else:
                merged_dict["achievement"] = 0

            result_list.append(merged_dict)

        return result_list, min_score, minium_achievement

    if len(charts_score_old) < 35:
        messages_list.append(
            {"type": "tips", "text": "目前游玩过的歌曲还不多，再打打再来吧！\n（推荐先游玩自己感兴趣的、喜欢的歌曲哦！）"}
        )
        return {
            "recommend_charts": [],
            "new_song_min_rating": -1,
            "old_song_min_rating": -1,
            "messages": messages_list,
        }
    else:
        old_songs_recommend, old_song_min_score, minium_achievement = _query_charts(
            is_new=False,
            charts_score=charts_score_old,
            filtered_song_ids=filtered_song_ids,
        )
    count_query = song_info.select().where(song_info.is_new == True).count()
    if count_query < 30 or len(charts_score_new) < 15:
        new_songs_recommend = []
        new_song_min_score = -1
    else:
        new_songs_recommend, new_song_min_score, minium_achievement = _query_charts(
            is_new=True,
            charts_score=charts_score_new,
            filtered_song_ids=filtered_song_ids,
        )

    if len(charts_score_new) < 15:
        messages_list.append(
            {"type": "tips", "text": "比起游玩已经更新许久的歌曲，似乎游玩刚刚更新的歌曲推分更有效率哦！"}
        )

    recommend_list = {
        "recommend_charts": old_songs_recommend + new_songs_recommend
        if new_songs_recommend
        else old_songs_recommend,
        "new_song_min_rating": new_song_min_score,
        "old_song_min_rating": old_song_min_score,
        "minium_achievement": minium_achievement,
        "messages": messages_list,
    }
    print(
        [
            f'{x["song_title"]}({x["level"]},{x["difficulty"]}/{x["achievement"]})'
            for x in recommend_list["recommend_charts"]
        ]
    )
    print(recommend_list)
    print("process time:", time.time() - time1)
    return recommend_list


with open("response.json") as f:
    c = json.load(f)

asyncio.run(recommend_charts(c))
