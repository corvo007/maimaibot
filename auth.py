from datetime import datetime, timedelta
from typing import Optional, Tuple

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from jose import ExpiredSignatureError, JWTError, jwt
from pydantic import ValidationError

from core import get_player_data_from_remote
from database import config
from exception import InvalidTokenError
from model import TokenDataModel

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")  # TODO:add token url


def get_current_player(token: str = Depends(oauth2_scheme)) -> dict:
    try:
        payload = jwt.decode(
            token,
            config.app.secret_key,
            algorithms=["HS256"],
            options={"verify_signature": True, "verify_exp": True, "require_exp": True},
        )
        username: str = payload.get("username")
        if username is None:
            raise InvalidTokenError
        token_data = TokenDataModel(username=username)
    except (JWTError, ValidationError, ExpiredSignatureError):
        raise InvalidTokenError

    return token_data.dict()


async def generate_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(days=7)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, config.app.secret_key, algorithm="HS256")
    return encoded_jwt


async def validate_player(username: str, bind_qq: int) -> Tuple[dict, dict]:
    result = await get_player_data_from_remote(username, bind_qq)
    access_token = await generate_token({"username": result["username"]})
    return {"access_token": access_token, "username": result["username"]}, result
