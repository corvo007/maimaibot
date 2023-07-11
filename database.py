import datetime
import json
import os
import time

import peewee
from playhouse.shortcuts import ReconnectMixin

from model import ConfigModel


class RetryMySQLDatabase(ReconnectMixin, peewee.MySQLDatabase):
    pass


with open(
    os.path.join(os.path.dirname(__file__), "config.json"), "r", encoding="utf-8"
) as f:
    config = ConfigModel.parse_obj(json.load(f))


class CustomJSONField(peewee.TextField):
    def db_value(self, value):
        """将Python字典转换为JSON字符串"""
        return json.dumps(value)

    def python_value(self, value):
        """将JSON字符串转换回Python字典"""
        return json.loads(value)


class LongText(peewee.Field):
    field_type = "LONGTEXT"


song_database = RetryMySQLDatabase(
    host=config.MySQL.MySQL_host,
    port=config.MySQL.MySQL_port,
    user=config.MySQL.MySQL_username,
    password=config.MySQL.MySQL_password,
    database=config.MySQL.MySQL_database,
    charset="utf8",
)


class BaseDatabase(peewee.Model):
    pass

    class Meta:
        database = song_database


class SongInfo(BaseDatabase):
    song_id = peewee.BigIntegerField(primary_key=True)

    artist = peewee.CharField()
    song_title = peewee.CharField()
    bpm = peewee.IntegerField()
    version = peewee.CharField()  # 更新版本
    genre = peewee.CharField()  # 流派
    is_new = peewee.BooleanField()  # 是否为当前版本歌曲
    type = peewee.IntegerField()  # 0:DX谱 1:标准谱


class ChartInfo(BaseDatabase):
    song_id = peewee.ForeignKeyField(SongInfo, on_delete="CASCADE")
    level = peewee.IntegerField()  # 1~5分别代表Basic~Re:Master

    chart_design = peewee.CharField()  # 谱师
    tap_note = peewee.IntegerField()
    hold_note = peewee.IntegerField()
    slide_note = peewee.IntegerField()
    touch_note = peewee.IntegerField()  # 仅DX谱有touch
    break_note = peewee.IntegerField()
    difficulty = peewee.DecimalField(decimal_places=1)
    old_difficulty = peewee.DecimalField(decimal_places=1)

    class Meta:
        primary_key = peewee.CompositeKey("song_id", "level")


class ChartStat(BaseDatabase):
    song_id = peewee.BigIntegerField()
    level = peewee.IntegerField()

    sample_num = peewee.IntegerField()
    fit_difficulty = peewee.DecimalField()
    avg_achievement = peewee.DecimalField()
    avg_dxscore = peewee.DecimalField()
    std_dev = peewee.DecimalField()
    achievement_dist = CustomJSONField()
    fc_dist = CustomJSONField()

    like = peewee.IntegerField(default=0)  # 点赞人数
    dislike = peewee.IntegerField(default=0)  # 点踩人数
    weight = peewee.DecimalField(default=1)  # 权重

    class Meta:
        primary_key = peewee.CompositeKey("song_id", "level")


class ChartRecord(BaseDatabase):
    player_id = peewee.CharField()  # 登录用户名
    song_id = peewee.IntegerField()
    level = peewee.IntegerField()
    type = peewee.IntegerField()

    achievement = peewee.DecimalField()
    rating = peewee.IntegerField()
    dxscore = peewee.IntegerField()
    fc_status = peewee.CharField()
    fs_status = peewee.CharField()

    record_time = peewee.DateTimeField(default=datetime.datetime.now())


class ChartBlacklist(BaseDatabase):
    player_id = peewee.CharField()
    song_id = peewee.IntegerField()
    level = peewee.IntegerField()
    reason = peewee.CharField()

    record_time = peewee.DateTimeField(default=datetime.datetime.now())

    class Meta:
        primary_key = peewee.CompositeKey("player_id", "song_id", "level")


class RatingRecord(BaseDatabase):
    player_id = peewee.CharField()
    old_song_rating = peewee.IntegerField()
    new_song_rating = peewee.IntegerField()

    record_time = peewee.DateTimeField(default=datetime.datetime.now())


class SongDataVersion(BaseDatabase):
    key = peewee.CharField(primary_key=True)
    value = peewee.CharField()


class ChartVoting(BaseDatabase):
    player_id = peewee.CharField()
    song_id = peewee.IntegerField()
    level = peewee.IntegerField()
    vote = peewee.IntegerField()  # 0:like 1:dislike

    class Meta:
        primary_key = peewee.CompositeKey("player_id", "song_id", "level")


class ExceptionRecord(BaseDatabase):
    id = peewee.CharField(primary_key=True)  # traceid
    type = peewee.CharField()  # 异常类型
    brief = peewee.CharField()  # 异常详情
    traceback = LongText()  # 堆栈跟踪,长文本类型
    time = peewee.TimestampField(default=time.time())
