import os
import peewee

DATABASE_FILE = os.path.join(os.path.dirname(__file__), "data.sqlite")

song_database = peewee.SqliteDatabase(  # 正式上线切换成MySQL
    database=DATABASE_FILE,
    pragmas={
        "journal_mode": "wal",
        "cache_size": -1024 * 64,
    },
)


class BaseDatabase(peewee.Model):
    pass
    
    class Meta:
        database = song_database


class song_info(BaseDatabase):
    song_id = peewee.BigIntegerField()
    level = peewee.IntegerField()  # 1~5分别代表Basic~Re:Master
    
    artist = peewee.CharField()
    song_title = peewee.CharField()
    bpm = peewee.IntegerField()
    version = peewee.CharField()  # 更新版本
    genre = peewee.CharField()  # 流派
    is_new = peewee.BooleanField()  # 是否为当前版本歌曲
    type = peewee.IntegerField()  # 0:DX谱 1:标准谱
    
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


class chart_stat(BaseDatabase):
    song_id = peewee.BigIntegerField()
    level = peewee.IntegerField()
    
    sample_num = peewee.IntegerField()
    fit_difficulty = peewee.DecimalField()
    avg_achievement = peewee.DecimalField()
    avg_dxscore = peewee.DecimalField()
    std_dev = peewee.DecimalField()
    achievement_dist = peewee.CharField()
    fc_dist = peewee.CharField()
    
    class Meta:
        primary_key = peewee.CompositeKey("song_id", "level")


class player_record(BaseDatabase):
    player_id = peewee.CharField()  # 登录用户名
    song_id = peewee.IntegerField()
    level = peewee.IntegerField()
    type = peewee.IntegerField()
    
    achievement = peewee.DecimalField()
    rating = peewee.IntegerField()
    dxscore = peewee.IntegerField()
    fc_status = peewee.IntegerField()
    fs_status = peewee.IntegerField()
