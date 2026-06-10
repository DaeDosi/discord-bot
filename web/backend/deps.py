from fastapi import HTTPException, Header
from jose import JWTError
from auth import decode_jwt


async def get_current_user(authorization: str = Header(...)) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")
    token = authorization[7:]
    try:
        payload = decode_jwt(token)
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="유효하지 않거나 만료된 토큰입니다.")
