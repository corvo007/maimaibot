from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, IPvAnyAddress, root_validator, validator

from const import *


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


class AppConfigModel(BaseModel):
    developer_token: str
    secret_key: str


class ConfigModel(BaseModel):
    MySQL: DataBaseConfigModel
    unicorn: UnicornConfigModel
    app: AppConfigModel


class PlayerPreferencesModel(BaseModel):
    recommend_preferences: Literal["aggressive", "balance", "conservative"] = "balance"
    exclude_played: bool = False


class BasicChartInfoModel(BaseModel):
    song_id: int = None
    level: int = None

    @root_validator(pre=True)
    def move_data_to_root(cls, values):
        data = values.pop("__data__", {})
        values.update(data)
        values.pop("_dirty", None)
        values.pop("__rel__", None)
        return values


class RecommendChartsModel(BasicChartInfoModel):
    vote: Optional[Literal[LIKE, DISLIKE, None]] = None


class DiffStatDataModel(BaseModel):
    achievements: float
    dist: List[float]
    fc_dist: List[float]


class AllDiffStatDataModel(BaseModel):
    diff_data: Dict[str, DiffStatDataModel]


class TokenDataModel(BaseModel):
    username: Optional[str] = None


class TokenModel(BaseModel):
    access_token: str
    token_type: str


class GeneralResponseModel(BaseModel):
    code: Optional[int] = 0
    data: Any = ""
    message: Optional[str] = "ok"


class GetDiffInputModel(BaseModel):
    upper_difficulty: Optional[float] = Field(15.0, ge=1.0, le=15.0)
    lower_difficulty: Optional[float] = Field(1.0, ge=1.0, le=15.0)
    limit: Optional[int] = Field(20, gt=0)

    @validator('lower_difficulty', pre=True, always=True)
    def lower_can_not_exceed_upper(cls, v, values, **kwargs):
        upper = values.get('upper_difficulty')
        if upper is not None and v > upper:
            raise ValueError('lower_difficulty can\'t be greater than upper_difficulty')
        return v
