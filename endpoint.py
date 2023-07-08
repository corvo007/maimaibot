import hashlib
from typing import Awaitable, Callable

from fastapi import APIRouter, Depends, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response, StreamingResponse

from core import *
from model import CompFilterModel, FilterModel, GeneralResponseModel

router = APIRouter(prefix="/api/v1/maimai")


async def async_generator(body):
    yield body


class ETagMiddleware(BaseHTTPMiddleware):
    exclude_paths = [
        "/login",
        "/another_path",
    ]  # add paths that you want to exclude here

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


# TODO:上线前检查忽略endpoint是否一致
@router.get("/basic_info")
async def _get_basic_info_frontend():
    basic_info = await get_basic_info_frontend()
    return GeneralResponseModel(data=basic_info)
