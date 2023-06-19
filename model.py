from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, IPvAnyAddress, root_validator, validator


class DataBaseConfigModel(BaseModel):
    MySQL_host: str
    MySQL_port: int
    MySQL_username: str
    MySQL_password: str
    MySQL_database: str


class UnicornConfigModel(BaseModel):
    bind_address: IPvAnyAddress
    bind_port: int
    reload: bool
    debug: bool


class ConfigModel(BaseModel):
    MySQL: DataBaseConfigModel
    unicorn: UnicornConfigModel


class player_preferences(BaseModel):
    recommend_preferences: Literal["aggressive", "balance", "conservative"] = "balance"
    exclude_played: bool = False


class RecommendSongsModel(BaseModel):
    song_id: int = None
    level: int = None
    chart_design: str = None
    tap_note: int = None
    hold_note: int = None
    slide_note: int = None
    touch_note: int = None
    break_note: int = None
    difficulty: float = None
    old_difficulty: float = None

    __data__: Dict[str, Any] = None
    _dirty: List[str] = None
    __rel__: Dict[str, Any] = None
    sample_num: int
    fit_difficulty: float
    avg_achievement: float
    avg_dxscore: float
    std_dev: float
    achievement_dist: List[int]
    fc_dist: List[float]
    like: int
    dislike: int
    weight: float
    artist: str
    song_title: str
    bpm: int
    version: str
    genre: str
    is_new: bool
    type: int
    vote: Optional[int]

    @root_validator(pre=True)
    def move_data_to_root(cls, values):
        data = values.pop("__data__", {})
        values.update(data)
        values.pop("_dirty", None)
        values.pop("__rel__", None)
        return values

    @validator("achievement_dist", "fc_dist", pre=True)
    def convert_string_to_list(cls, value):
        return [float(num) for num in value.strip("[]").split(",")]
