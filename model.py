from pydantic import BaseModel, IPvAnyAddress


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
