class Error(Exception):
    """错误基类"""
    
    def __init__(self, message: str):
        self.message = message
    
    def __str__(self):
        return f"遇到了错误：\n{self.message}"

