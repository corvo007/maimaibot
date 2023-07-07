import asyncio

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from database import BaseDatabase, config, song_database
from endpoint import ETagMiddleware, router
from log import logger
from core import (
    check_song_update,
    check_update_on_startup,
    run_chart_stat_update,
    update_public_player_rating,
)

app = FastAPI(title="maibot")
app.include_router(router)
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


if __name__ == "__main__":
    uvicorn.run(
        app="main:app",
        host=config.unicorn.bind_address,
        port=config.unicorn.bind_port,
        reload=config.unicorn.reload,
        debug=config.unicorn.debug,
    )
