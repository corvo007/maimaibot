import asyncio
import traceback
import uuid
from functools import wraps
from typing import Tuple

import httpx
import numpy as np
import scipy.stats as stats
from cachetools import TTLCache
from peewee import JOIN, fn

from database import *
from exception import ParameterError
from log import logger
from model import *

general_stat = {}
new_song_id = []
best_fit = None  # 拟合模型参数


class BestFitDistribution:
    def __init__(self, data):
        self.data = data
        self.distribution, self.params = self._select_best_fit(data)

    def _fit_distributions(self, data):
        lognorm_params = stats.lognorm.fit(data, floc=0)
        gamma_params = stats.gamma.fit(data, floc=0)
        weibull_params = stats.weibull_min.fit(data, floc=0)
        return lognorm_params, gamma_params, weibull_params

    def _select_best_fit(self, data):
        distribution_params = self._fit_distributions(data)
        aics = []
        distributions = [stats.lognorm, stats.gamma, stats.weibull_min]

        for params, dist in zip(distribution_params, distributions):
            log_likelihood = np.sum(dist.logpdf(data, *params))
            k = len(params)
            aic = 2 * k - 2 * log_likelihood
            aics.append(aic)

        best_fit_idx = np.argmin(aics)
        best_fit_params = distribution_params[best_fit_idx]
        best_fit_distribution = distributions[best_fit_idx]
        return best_fit_distribution, best_fit_params

    def percentile(self, new_data):
        percentile = self.distribution.cdf(new_data, *self.params) * 100
        return percentile


class AsyncTTLCache(TTLCache):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock = asyncio.Lock()

    async def get(self, key):
        async with self._lock:
            return super().get(key)

    async def pop(self, key):
        async with self._lock:
            return super().pop(key)

    async def set(self, key, value):
        async with self._lock:
            super().__setitem__(key, value)


def async_ttl_cache(cache):
    def decorator(func):
        @wraps(func)
        async def wrapped(*args, **kwargs):
            serialized_args = json.dumps(args, sort_keys=True)
            serialized_kwargs = json.dumps(kwargs, sort_keys=True)
            key = (serialized_args, serialized_kwargs)
            if await cache.get(key) is None:
                value = await func(*args, **kwargs)
                await cache.set(key, value)
            else:
                value = await cache.get(key)
            return value

        return wrapped

    return decorator


basic_info_cache = AsyncTTLCache(maxsize=100, ttl=43200)  # 歌曲及谱面基本信息缓存12小时
stat_cache = AsyncTTLCache(maxsize=150, ttl=1800)  # 统计信息缓存30分钟
player_record_cache = AsyncTTLCache(maxsize=250, ttl=300)  # 缓存5分钟


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
            local_version = SongDataVersion.get_or_none(key="version").value
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


def update_new_song_id():
    global new_song_id
    new_song_id = [
        i["song_id"]
        for i in list(
            SongInfo.select(SongInfo.song_id).where(SongInfo.is_new == True).dicts()
        )
    ]


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

    SongInfo.replace_many(songs_data).execute()
    ChartInfo.replace_many(charts_data).execute()
    SongDataVersion.replace(key="version", value=new_version).execute()


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
    general_stat = AllDiffStatDataModel.parse_obj(resp).dict()
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
    ChartStat.replace_many(chart_stats).execute()


def separate_personal_data(personal_raw_data: dict) -> Tuple[List, List]:
    personal_raw_data = personal_raw_data["records"]
    new_charts = filter(lambda x: x["song_id"] in new_song_id, personal_raw_data)
    old_charts = filter(lambda x: x["song_id"] not in new_song_id, personal_raw_data)
    return list(old_charts), list(new_charts)


async def record_player_data(personal_raw_data: dict) -> None:
    # 后台任务
    # TODO:去重（指去掉achievement相同的歌）
    player_id = personal_raw_data["username"]
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
    RatingRecord.replace(
        {
            "player_id": player_id,
            "old_song_rating": old_rating,
            "new_song_rating": new_rating,
        }
    ).execute()
    ChartRecord.replace_many(charts_list).execute()


@async_ttl_cache(player_record_cache)
async def get_player_data_from_remote(
    player_id: Optional[str] = None, bind_qq: Optional[int] = None
) -> dict:
    if player_id:
        params = {player_id: player_id}
    elif bind_qq:
        params = {"bind_qq": bind_qq}
    else:
        raise ParameterError
    # TODO: remove debug code
    with open("response.json") as f:
        return json.load(f)
    """async with httpx.AsyncClient(timeout=10) as client:
        resp = (
            await client.post(
                PLAYER_DATA_DEV_API,
                params=params,
                headers={"developer-token": config.app.developer_token},
            )
        )
        if resp.status_code == 400:
            raise NoSuchPlayerError
    return resp.json()"""


@async_ttl_cache(player_record_cache)
async def recommend_charts(
    personal_raw_data: dict,
    preferences: PlayerPreferencesModel = None,
    limit: int = 50,
) -> dict:
    messages_list = []
    if preferences is None:
        preferences = PlayerPreferencesModel.parse_obj(dict())
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
                median_score * 100 / 99.00 / SONG_RATING_COEFFICIENT[-6][1]
            )
            lower_difficulty = (
                (min_score + 1) * 100 / 99.50 / SONG_RATING_COEFFICIENT[-5][1]
            )
            minium_achievement = 99.0000
        elif preferences.recommend_preferences == "conservative":
            # min:SS+ Top(99.99%) max:SSS+(100.50%)
            upper_difficulty = (
                median_score * 100 / 99.99 / SONG_RATING_COEFFICIENT[-4][1]
            )
            lower_difficulty = (
                (min_score + 1) * 100 / 100.50 / SONG_RATING_COEFFICIENT[-1][1]
            )
            minium_achievement = 100.0000
        else:
            # min:S(97.00%) max:S+(98.00%)
            upper_difficulty = (
                median_score * 100 / 97.00 / SONG_RATING_COEFFICIENT[-8][1]
            )
            lower_difficulty = (
                (min_score + 1) * 100 / 98.00 / SONG_RATING_COEFFICIENT[-7][1]
            )
            minium_achievement = 97.0000

        if lower_difficulty > upper_difficulty:
            lower_difficulty, upper_difficulty = upper_difficulty, lower_difficulty

        upper_difficulty = float(upper_difficulty)
        lower_difficulty = float(lower_difficulty)

        base_condition = ChartInfo.difficulty.between(
            lower_difficulty, upper_difficulty
        )

        grade_condition = (
            (SongInfo.is_new == True) if is_new else (SongInfo.is_new == False)
        )

        # 使用 IF 函数，当 like 和 dislike 之和小于5时设为 0.5，否则计算比例
        like_dislike_ratio = fn.IF(
            (ChartStat.like + ChartStat.dislike) < 5,
            0.5,
            ChartStat.like / (ChartStat.like + ChartStat.dislike),
        )

        # 构建排序计算公式
        order_expression = (
            ChartInfo.difficulty - ChartStat.fit_difficulty + like_dislike_ratio
        ) * ChartStat.weight

        query = (
            ChartInfo.select(
                ChartInfo.song_id,
                ChartInfo.level,
                ChartVoting.vote,
            )
            .join(
                ChartStat,
                on=(ChartInfo.song_id == ChartStat.song_id)
                & (ChartInfo.level == ChartStat.level),
            )
            .join(SongInfo, on=(ChartInfo.song_id == SongInfo.song_id))
            .join(
                ChartBlacklist,
                on=(
                    (ChartInfo.song_id == ChartBlacklist.song_id)
                    & (ChartInfo.level == ChartBlacklist.level)
                    & (ChartBlacklist.player_id == player_id)
                ),
                join_type=JOIN.LEFT_OUTER,
            )
            .join(
                ChartVoting,
                on=(
                    (ChartInfo.song_id == ChartVoting.song_id)
                    & (ChartInfo.level == ChartVoting.level)
                    & (ChartVoting.player_id == player_id)
                ),
                join_type=JOIN.LEFT_OUTER,
            )  # 添加LEFT OUTER JOIN连接chart_voting表
            .where(
                (base_condition & grade_condition)
                & (ChartBlacklist.player_id.is_null())
                & (~(ChartInfo.song_id << filtered_song_ids))
            )
            .where(ChartStat.sample_num >= 100)
            .order_by(order_expression.desc())
            .limit(limit)
        )

        # 查询结果转换为包含字典的列表
        result_list = []
        for record in query.objects():
            merged_dict = RecommendChartsResultModel.parse_obj(record.__dict__).dict()
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
    count_query = SongInfo.select().where(SongInfo.is_new == True).count()
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
        ChartBlacklist.replace(
            player_id=player_id, song_id=song_id, level=level, reason=reason
        ).execute()
    else:
        ChartBlacklist.delete().where(
            player_id=player_id, song_id=song_id, level=level
        ).execute()


async def get_blacklist(player_id: str) -> list:
    return list(ChartBlacklist.select().where(player_id == player_id).dicts())


async def vote_songs(
    player_id: str, song_id: int, level: int, operate: Literal[LIKE, DISLIKE]
) -> None:
    ChartVoting.replace(
        player_id=player_id, song_id=song_id, level=level, vote=operate
    ).execute()


async def get_all_level_stat():
    return general_stat


@async_ttl_cache(stat_cache)
async def get_difficulty_difference(
    upper_difficulty: Optional[float] = 15.0,
    lower_difficulty: Optional[float] = 11.0,
    limit: int = 20,
) -> List[dict]:
    result = []
    query = (
        ChartInfo.select(ChartInfo.song_id, ChartInfo.level)
        .where(ChartInfo.old_difficulty != -1)
        .where(
            (ChartInfo.difficulty >= lower_difficulty)
            & (ChartInfo.difficulty <= upper_difficulty)
        )
        .order_by((ChartInfo.difficulty - ChartInfo.old_difficulty).desc())
        .limit(limit)
    )

    for chart in query.objects():
        result.append(BasicChartInfoModel.parse_obj(chart.__dict__).dict())

    return result


@async_ttl_cache(stat_cache)
async def get_most_popular_songs(
    upper_difficulty: Optional[float] = 15.0,
    lower_difficulty: Optional[float] = 11.0,
    chart_type: Optional[int] = None,
    genre: Optional[str] = None,
    version: Optional[str] = None,
    limit: int = 20,
):
    result = []
    query = (
        ChartInfo.select(ChartInfo.song_id, ChartInfo.level)
        .join(
            ChartStat,
            on=(
                (ChartInfo.song_id == ChartStat.song_id)
                & (ChartInfo.level == ChartStat.level)
            ),
        )
        .switch(ChartInfo)
        .join(SongInfo, on=(ChartInfo.song_id == SongInfo.song_id))
        .where(
            (ChartInfo.difficulty >= lower_difficulty)
            & (ChartInfo.difficulty <= upper_difficulty)
        )
    )

    if isinstance(chart_type, int):
        query = query.where(SongInfo.type == chart_type)

    if genre:
        query = query.where(SongInfo.genre == genre)

    if version:
        query = query.where(SongInfo.version == version)

    query = query.order_by(ChartStat.sample_num.desc()).limit(limit)

    for chart in query.objects():
        result.append(BasicChartInfoModel.parse_obj(chart.__dict__).dict())

    return result


@async_ttl_cache(stat_cache)
async def get_relative_easy_or_hard_songs(
    upper_difficulty: Optional[float] = 15.0,
    lower_difficulty: Optional[float] = 11.0,
    limit: int = 20,
) -> dict:
    result = {"easy": [], "hard": []}
    basic_query = (
        ChartInfo.select(ChartInfo.song_id, ChartInfo.level)
        .join(
            ChartStat,
            on=(
                (ChartInfo.song_id == ChartStat.song_id)
                & (ChartInfo.level == ChartStat.level)
            ),
        )
        .where(
            (ChartInfo.difficulty >= lower_difficulty)
            & (ChartInfo.difficulty <= upper_difficulty)
        )
        .where(ChartStat.sample_num >= 100)
    )

    desc_query = basic_query.order_by(
        (ChartStat.fit_difficulty - ChartInfo.difficulty).desc()
    ).limit(limit)
    asc_query = basic_query.order_by(
        (ChartStat.fit_difficulty - ChartInfo.difficulty).asc()
    ).limit(limit)

    for chart in desc_query.objects():
        result["hard"].append(BasicChartInfoModel.parse_obj(chart.__dict__).dict())

    for chart in asc_query.objects():
        result["easy"].append(BasicChartInfoModel.parse_obj(chart.__dict__).dict())

    return result


@async_ttl_cache(stat_cache)
async def get_biggest_deviation_songs(
    upper_difficulty: Optional[float] = 15.0,
    lower_difficulty: Optional[float] = 11.0,
    chart_type: Optional[int] = None,
    genre: Optional[str] = None,
    version: Optional[str] = None,
    limit: int = 20,
):
    result = []
    query = (
        ChartInfo.select(ChartInfo.song_id, ChartInfo.level)
        .join(
            ChartStat,
            on=(
                (ChartInfo.song_id == ChartStat.song_id)
                & (ChartInfo.level == ChartStat.level)
            ),
        )
        .switch(ChartInfo)
        .join(SongInfo, on=(ChartInfo.song_id == SongInfo.song_id))
        .where(
            (ChartInfo.difficulty >= lower_difficulty)
            & (ChartInfo.difficulty <= upper_difficulty)
        )
        .where(ChartStat.sample_num >= 100)
    )

    if isinstance(chart_type, int):
        query = query.where(SongInfo.type == chart_type)

    if genre:
        query = query.where(SongInfo.genre == genre)

    if version:
        query = query.where(SongInfo.version == version)

    query = query.order_by(ChartStat.std_dev.desc()).limit(limit)

    for chart in query.objects():
        result.append(BasicChartInfoModel.parse_obj(chart.__dict__).dict())

    return result


async def get_player_record(player_id: str):
    # TODO:后端区分新旧曲，按rating排序
    # 如果是从api直接获取数据，那么看不到比最好成绩差的成绩
    chart_result = {}
    rating_result = []
    charts_records = (
        ChartRecord.select()
        .where(ChartRecord.player_id == player_id)
        .order_by(ChartRecord.record_time.desc())
    )
    for r in charts_records:
        keys = f"{r.song_id}-{r.level}"
        if keys not in chart_result:
            chart_result[keys] = []
        chart_result[keys].append(
            {
                "type": r.type,
                "achievement": float(r.achievement),
                "rating": r.rating,
                "dxscore": r.dxscore,
                "fc_status": r.fc_status,
                "fs_status": r.fs_status,
                "record_time": r.record_time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    rating_records = (
        RatingRecord.select()
        .where(RatingRecord.player_id == player_id)
        .order_by(RatingRecord.record_time.desc())
    )
    for r in rating_records:
        rating_result.append(
            {
                "old_song_rating": r.old_song_rating,
                "new_song_rating": r.new_song_rating,
                "record_time": r.record_time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    if len(rating_result) >= 1:
        try:
            rating_percentile = round(
                await get_player_percentile(
                    rating_result[0]["old_song_rating"]
                    + rating_result[0]["new_song_rating"]
                ),
                2,
            )
        except Exception as e:
            rating_percentile = "N/A"
    else:
        rating_percentile = "N/A"
    result = {
        "rating_records": rating_result,
        "charts_records": chart_result,
        "rating_percentile": rating_percentile if rating_percentile else "N/A",
    }

    return result


@async_ttl_cache(basic_info_cache)
async def get_basic_info_frontend():
    query_results = (
        SongInfo.select(SongInfo, ChartInfo, ChartStat)
        .join(ChartInfo, on=(SongInfo.song_id == ChartInfo.song_id))
        .switch(SongInfo)
        .join(
            ChartStat,
            on=(
                (SongInfo.song_id == ChartStat.song_id)
                & (ChartInfo.level == ChartStat.level)
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


async def update_public_player_rating() -> None:
    global best_fit
    logger.info("updating player ranking")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = (await client.get(PLAYER_RANKING_API)).json()
    except Exception as e:
        logger.exception(e)
        logger.critical(
            f"Error <{e}> encountered while checking update for chart statistics"
        )
        return
    data = []
    for i in resp:
        data.append(i["ra"])
    best_fit = BestFitDistribution(data)


async def get_player_percentile(player_rating: int) -> Optional[float]:
    if hasattr(best_fit, "percentile"):
        return best_fit.percentile(player_rating)
    else:
        return None


async def check_update_on_startup() -> None:
    await asyncio.sleep(25)
    await check_song_update()
    await run_chart_stat_update()
    await update_public_player_rating()


async def record_exception(e: Exception) -> str:
    trace_id = str(uuid.uuid4())
    try:
        exception_type = type(e).__name__
        exception_traceback = traceback.format_exc()
        ExceptionRecord.replace(
            id=trace_id,
            type=exception_type,
            brief=repr(e),
            traceback=exception_traceback,
            time=int(time.time()),
        ).execute()
    except Exception as e:
        internal_trace_id = str(uuid.uuid4())
        logger.exception(
            f"detail information of internal trace id :{internal_trace_id} (recorded internal exception)\n"
        )
    return trace_id
