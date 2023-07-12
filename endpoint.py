import hashlib
import typing
from typing import Awaitable, Callable

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response, StreamingResponse

from core import *
from model import *

charts_router = APIRouter(prefix="/api/v1/maimai/charts")
player_router = APIRouter(prefix="/api/v1/maimai/player")


class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        elif isinstance(obj, np.generic):
            return obj.item()
        return super().default(obj)


class CustomJSONResponse(JSONResponse):
    def render(self, content: typing.Any) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
            cls=CustomJSONEncoder,
        ).encode("utf-8")


import time


class TokenBucket(object):
    def __init__(self, rate, capacity):
        self._rate = rate
        self._capacity = capacity
        self._tokens = 0
        self._last = 0

    def consume(self):
        now = int(time.time())
        lapse = now - self._last
        self._last = now
        self._tokens += lapse * self._rate
        self._tokens = min(self._tokens, self._capacity)
        if self._tokens >= 1:
            self._tokens -= 1
            return True
        return False


class RateLimiter(object):
    def __init__(self, rate, capacity):
        self._default_bucket = TokenBucket(rate, capacity)
        self._buckets = {}

    def get_bucket(self, key, rate=None, capacity=None):
        if key not in self._buckets:
            self._buckets[key] = TokenBucket(
                rate or self._default_bucket._rate,
                capacity or self._default_bucket._capacity,
            )
        return self._buckets[key]

    def is_allowed(self, key):
        return self.get_bucket(key).consume()


class ThrottlingMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, default_rate, default_capacity, config=None):
        super().__init__(app)
        self.default_rate = default_rate
        self.default_capacity = default_capacity
        self.config = config or {}
        self.rate_limiter = RateLimiter(default_rate, default_capacity)

    async def dispatch(self, request: Request, call_next):
        # If the request is forwarded from a proxy server like Nginx,
        # we should get the client's original IP from 'x-forwarded-for' header.
        client_ip = request.headers.get("x-forwarded-for") or request.client.host

        path = str(request.url.path)
        client_path = path + client_ip  # combining path and client ip

        rate = self.config.get(path, {}).get("rate", self.default_rate)
        capacity = self.config.get(path, {}).get("capacity", self.default_capacity)
        bucket = self.rate_limiter.get_bucket(client_path, rate, capacity)

        if not bucket.consume():
            retry_after = int(1 / rate)
            response = JSONResponse(
                content=GeneralResponseModel(
                    message=f"Too many requests from {client_ip}. Try again after {retry_after} seconds."
                ).dict(),
                headers={"Retry-After": str(retry_after)},
                status_code=429,
            )
        else:
            response = await call_next(request)

        return response


async def async_generator(body):
    yield body


class ETagMiddleware(BaseHTTPMiddleware):
    exclude_paths = ["/set_account"]

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ):
        # Check if the request is a GET request
        if request.method.lower() != "get":
            return await call_next(request)

        # Check if the requested path should be excluded
        if request.url.path in self.exclude_paths:
            return await call_next(request)

        # First, let's call the real route handler
        real_response = await call_next(request)

        # Check if the response is not 200
        if real_response.status_code != 200:
            return real_response

        if isinstance(real_response, StreamingResponse):
            body = b"".join([part async for part in real_response.body_iterator])

            # Compute ETag
            etag = hashlib.md5(body).hexdigest()
            if (
                "if-none-match" in request.headers
                and request.headers["if-none-match"] == etag
            ):
                return Response(status_code=304)

            # Rebuild the streaming response with the new body and headers
            real_response = StreamingResponse(
                async_generator(body),
                media_type=real_response.media_type,
                headers={
                    **real_response.headers,
                    "ETag": etag,
                },  # Include original headers and ETag
            )

        # For other response types, proceed as before
        else:
            body = real_response.body

            # Compute ETag
            etag = hashlib.md5(body).hexdigest()
            if (
                "if-none-match" in request.headers
                and request.headers["if-none-match"] == etag
            ):
                return Response(status_code=304)

            # Add the ETag to the existing headers
            real_response.headers["ETag"] = etag

        return real_response


@charts_router.get("/basic_info")
async def _get_basic_info_frontend():
    basic_info = await get_basic_info_frontend()
    return GeneralResponseModel(data=basic_info)


@charts_router.get("/difficulty_difference")
async def _get_difficulty_difference(query: FilterModel = Depends()):
    result = await get_difficulty_difference(**query.dict())
    return GeneralResponseModel(data=result)


@charts_router.get("/biggest_deviation")
async def _get_biggest_deviation_songs(query: CompFilterModel = Depends()):
    result = await get_biggest_deviation_songs(**query.dict())
    return GeneralResponseModel(data=result)


@charts_router.get("/relative_easy_hard")
async def _get_relative_easy_or_hard_songs(query: FilterModel = Depends()):
    result = await get_relative_easy_or_hard_songs(**query.dict())
    return GeneralResponseModel(data=result)


@charts_router.get("/most_popular")
async def _get_most_popular_songs(query: CompFilterModel = Depends()):
    result = await get_most_popular_songs(**query.dict())
    return GeneralResponseModel(data=result)


@charts_router.get("/all_level_stat")
async def _get_all_level_stat():
    return GeneralResponseModel(data=await get_all_level_stat())


@player_router.get("/recommend_chart")
async def _recommend_chart(
    background_tasks: BackgroundTasks,
    query: RecommendChartsModel = Depends(),
):
    query_result = await get_player_data_from_remote(query.bind_qq, query.username)
    background_tasks.add_task(record_player_data, query_result)
    recommend = await recommend_charts(query_result, query.preferences, query.limit)
    return CustomJSONResponse({"code": 0, "data": recommend, "message": "ok"})


@player_router.post("/blacklist")
async def _modify_blacklist(query: OperateBlacklistModel = Depends()):
    result = await operate_blacklist(**query.dict())
    return GeneralResponseModel(data=result)


@player_router.get("/blacklist")
async def _get_blacklist(query: OnlyPlayeridModel = Depends()):
    await operate_blacklist(**query.dict())
    return GeneralResponseModel()


@player_router.post("/vote_songs")
async def _vote_songs(query: VoteSongsModel = Depends()):
    await vote_songs(**query.dict())
    return GeneralResponseModel()


@player_router.get("/record")
async def _get_player_record(query: OnlyPlayeridModel = Depends()):
    # TODO:流式传输/分页？
    result = await get_player_record(**query.dict())
    return GeneralResponseModel(data=result)


@player_router.post("/sync_record")
async def _sync_player_record(query: PlayerInfoModel = Depends()):
    # TODO:流式传输/分页？
    query_result = await get_player_data_from_remote(query.bind_qq, query.username)
    await record_player_data(query_result)
    result = await get_player_record(query_result["username"])
    return GeneralResponseModel(data=result)
