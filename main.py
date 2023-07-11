import asyncio

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.responses import JSONResponse

from core import (
    check_song_update,
    check_update_on_startup,
    record_exception,
    run_chart_stat_update,
    update_new_song_id,
    update_public_player_rating,
)
from database import BaseDatabase, config, song_database
from endpoint import ETagMiddleware, charts_router, player_router
from exception import *
from log import logger

app = FastAPI(title="maibot")
app.include_router(charts_router)
app.include_router(player_router)
app.add_middleware(ETagMiddleware)
scheduler = AsyncIOScheduler()


@app.on_event("startup")
async def initialize_database():
    logger.info(f"Initializing Database...")
    song_database.connect()
    for table in BaseDatabase.__subclasses__():
        try:
            if getattr(table, "is_deprecated", False):
                continue
            if not table.table_exists():
                table.create_table()
                logger.info(f"Table <{table.__name__}> not exists, will be created.")
        except Exception as e:
            logger.exception(e)
            logger.critical(
                f"Error <{e}> encountered while initializing table <{table.__name__}>."
            )
    song_database.close()


@app.on_event("startup")
async def _check_update_on_startup() -> None:
    update_new_song_id()
    asyncio.create_task(check_update_on_startup())


@app.on_event("startup")
async def check_update_regularly() -> None:
    scheduler.add_job(
        check_song_update, "interval", hours=12, max_instances=1, misfire_grace_time=10
    )
    # 谱面/歌曲信息12小时检查更新一次
    scheduler.add_job(
        run_chart_stat_update,
        "interval",
        minutes=30,
        max_instances=1,
        misfire_grace_time=10,
    )  # 谱面/歌曲统计30分钟更新一次
    scheduler.add_job(
        update_public_player_rating,
        "interval",
        hours=12,
        max_instances=1,
        misfire_grace_time=10,
    )
    scheduler.start()


@app.exception_handler(NoSuchPlayerError)
@app.exception_handler(404)
async def _handle_404(request: Request, exc: Exception):
    error_msg = (
        "未找到玩家信息，请确认输入是否正确。" if isinstance(exc, NoSuchPlayerError) else f"未找到你所请求的网页。"
    )
    return JSONResponse(
        status_code=404,
        content={"code": -404, "data": {}, "message": error_msg},
        media_type="application/json",
    )


@app.exception_handler(405)
async def _handle_405(request: Request, exc: Exception):
    return JSONResponse(
        status_code=405,
        content={"code": -405, "data": {}, "message": "不支持请求的方法"},
        media_type="application/json",
    )


@app.exception_handler(InvalidTokenError)
@app.exception_handler(401)
async def _handle_401(request: Request, exc: Exception):
    if hasattr(exc, "message"):
        error_msg = f"{exc.message}\n请求URL：{request.url}"
    else:
        error_msg = f"凭证无效。\n请求URL：{request.url}"
    return JSONResponse(
        status_code=401,
        content={"code": -401, "data": {}, "message": error_msg},
        media_type="application/json",
    )


@app.exception_handler(500)
async def _handle_500(request: Request, exc: Exception):
    trace_id = await record_exception(exc)
    return JSONResponse(
        status_code=500,
        content={
            "code": -500,
            "data": {},
            "message": f"发生了内部错误，请稍后重试。\ntrace_id:{trace_id}",
        },
        media_type="application/json",
    )


@app.exception_handler(ValidationError)
@app.exception_handler(ParameterError)
@app.exception_handler(RequestValidationError)
async def _handle_422(request: Request, exc: Exception):
    if hasattr(exc, "errors"):
        error_msg = ""
        for i in exc.errors():
            error_msg += f'{i["msg"]}\n'
    elif hasattr(exc, "message"):
        error_msg = exc.message
    else:
        error_msg = ""
    return JSONResponse(
        status_code=422,
        content={
            "code": -422,
            "data": {},
            "message": f"查询参数无效。\n详情:{error_msg}",
        },
        media_type="application/json",
    )


if __name__ == "__main__":
    uvicorn.run(
        app="main:app",
        host=str(config.unicorn.bind_address),
        port=config.unicorn.bind_port,
        reload=config.unicorn.reload,
        debug=config.unicorn.debug,
        log_level="debug",
    )
