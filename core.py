import asyncio
import decimal
import json
import time
import traceback
from typing import List, Literal, Optional, Tuple

import httpx
import numpy as np
from peewee import JOIN, SQL, fn
from pydantic import ValidationError

from database import (
    chart_blacklist,
    chart_info,
    chart_record,
    chart_stat,
    chart_voting,
    rating_record,
    song_data_version,
    song_info,
)
from exception import ParameterError
from log import logger
from model import (
    AllDiffStatData,
    BasicChartInfoModel,
    RecommendChartsModel,
    player_preferences,
)
from const import *


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
            local_version = song_data_version.get_or_none(key="version").value
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
            "type": DX_CHART if song["type"] == "DX" else STD_CHART,
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
    song_data_version.replace(key="version", value=new_version).execute()


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
    general_stat = AllDiffStatData.parse_obj(resp).dict()
    for k, v in resp["charts"].items():
        for index, chart in enumerate(v):
            if not chart:
                continue
            chart_stats.append(
                {
                    "song_id": int(k),
                    "level": index + 1,
                    "sample_num": chart["cnt"],
                    "fit_difficulty": round(float(chart["fit_diff"]), ndigits=5),
                    "avg_achievement": round(float(chart["avg"]), ndigits=5),
                    "avg_dxscore": round(float(chart["avg_dx"]), ndigits=5),
                    "std_dev": round(float(chart["std_dev"]), ndigits=5),
                    "achievement_dist": chart["dist"],
                    "fc_dist": chart["fc_dist"],
                }
            )
    chart_stat.replace_many(chart_stats).execute()


"""
[float(num) for num in chart["dist"].strip("[]").split(",")]
"""


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
                "type": STD_CHART if charts["type"] == "SD" else DX_CHART,
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
    limit: int = 50,
) -> dict:
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
    personal_grades_dict = {}

    for score in personal_raw_data:
        personal_grades_dict[(score["song_id"], score["level_index"])] = score
        if score["achievements"] >= 100.5000:
            filtered_song_ids.append(score["song_id"])
            continue
        if preferences.exclude_played and score["achievements"] >= 94:
            filtered_song_ids.append(score["song_id"])

    async def _query_charts(
        is_new: bool, charts_score: list, filtered_song_ids: list
    ) -> Tuple[List[dict], int, int]:
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
            chart_info.select(
                chart_info.song_id,
                chart_info.level,
                chart_voting.vote,
            )
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
            .join(
                chart_voting,
                on=(
                    (chart_info.song_id == chart_voting.song_id)
                    & (chart_info.level == chart_voting.level)
                    & (chart_voting.player_id == player_id)
                ),
                join_type=JOIN.LEFT_OUTER,
            )  # 添加LEFT OUTER JOIN连接chart_voting表
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
        for record in query.objects():
            merged_dict = RecommendChartsModel.parse_obj(record.__dict__).dict()
            if not (
                _grade := personal_grades_dict.get(
                    (merged_dict["song_id"], merged_dict["level"] - 1), None
                )
            ):
                merged_dict["achievement"] = 0
            else:
                merged_dict["achievement"] = _grade["achievements"]

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
        (
            old_songs_recommend,
            old_song_min_score,
            minium_achievement,
        ) = await _query_charts(
            is_new=False,
            charts_score=charts_score_old,
            filtered_song_ids=filtered_song_ids,
        )
    count_query = song_info.select().where(song_info.is_new == True).count()
    if count_query < 30:
        new_songs_recommend = []
        new_song_min_score = np.min(charts_score_new)
    elif len(charts_score_new) < 15:
        new_song_min_score = -1
        new_songs_recommend = []
    else:
        (
            new_songs_recommend,
            new_song_min_score,
            minium_achievement,
        ) = await _query_charts(
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

    return recommend_list


async def operate_blacklist(
    player_id: str,
    song_id: int,
    level: int,
    operate: Literal["add", "delete"],
    reason: str,
):
    if operate == "add":
        chart_blacklist.replace(
            player_id=player_id, song_id=song_id, level=level, reason=reason
        ).execute()
    else:
        chart_blacklist.delete().where(
            player_id=player_id, song_id=song_id, level=level
        ).execute()


async def get_blacklist(player_id: str) -> list:
    return list(chart_blacklist.select().where(player_id == player_id).dicts())


async def set_like(player_id: str, song_id: int, level: int) -> None:
    chart_voting.replace(
        player_id=player_id, song_id=song_id, level=level, vote=LIKE
    ).execute()


async def set_dislike(player_id: str, song_id: int, level: int) -> None:
    chart_voting.replace(
        player_id=player_id, song_id=song_id, level=level, vote=DISLIKE
    ).execute()


async def get_all_level_stat():
    return general_stat


async def get_difficulty_difference(
    difficulty_range: Optional[list] = None, limit: int = 10
) -> List[dict]:
    result = []
    difficulty_range = [11.0, 15.0] if not difficulty_range else difficulty_range
    min_difficulty, max_difficulty = min(difficulty_range), max(difficulty_range)
    query = (
        chart_info.select(chart_info.song_id, chart_info.level)
        .where(chart_info.old_difficulty != -1)
        .where(
            (chart_info.difficulty >= min_difficulty)
            & (chart_info.difficulty <= max_difficulty)
        )
        .order_by((chart_info.difficulty - chart_info.old_difficulty).desc())
        .limit(limit)
    )

    for chart in query.objects():
        result.append(BasicChartInfoModel.parse_obj(chart.__dict__).dict())

    return result


async def get_most_popular_songs():
    pass


async def get_relative_easy_songs():
    pass


async def get_relative_hard_songs():
    pass


async def get_biggest_deviation_songs():
    pass


async def get_player_record():
    pass


async def get_basic_info_frontend():
    query_results = (
        song_info.select(song_info, chart_info, chart_stat)
        .join(chart_info, on=(song_info.song_id == chart_info.song_id))
        .switch(song_info)
        .join(
            chart_stat,
            on=(
                (song_info.song_id == chart_stat.song_id)
                & (chart_info.level == chart_stat.level)
            ),
        )
        .dicts()
    )  # 结果会以字典的格式返回

    result_dict = {}

    for chart in query_results:
        key = f"{chart['song_id']}-{chart['level']}"  # 将键转换为字符串格式，如 "1-3"

        del chart["song_id"]
        del chart["level"]

        result_dict[key] = chart

    return result_dict

