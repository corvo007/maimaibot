import asyncio

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from database import BaseDatabase, config, song_database
from log import logger
from core import check_song_update, run_chart_stat_update

app = FastAPI(title="maibot")
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
            logger.critical(f"Error <{e}> encountered while initializing table <{table.__name__}>.")
    song_database.close()


@app.on_event("startup")
async def check_update_on_startup() -> None:
    await asyncio.sleep(15)
    await check_song_update()
    await run_chart_stat_update()


if __name__ == "__main__":
    uvicorn.run(
        app="main:app",
        host=config.unicorn.bind_address,
        port=config.unicorn.bind_port,
        reload=config.unicorn.reload,
        debug=config.unicorn.debug,
    )
