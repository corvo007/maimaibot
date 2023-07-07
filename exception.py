class Error(Exception):
    """错误基类"""

    def __init__(self, message: str):
        self.message = message

    def __str__(self):
        return f"遇到了错误：\n{self.message}"


class ParameterError(Error):
    def __init__(self, message: str = "参数错误"):
        self.message = message


class NoSuchPlayerError(Error):
    def __init__(self, message: str = "无此玩家，请检查输入"):
        self.message = message


class InvalidTokenError(Error):
    def __init__(self, message: str = "凭证无效"):
        self.message = message
