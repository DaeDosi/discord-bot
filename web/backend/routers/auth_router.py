from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import JSONResponse, RedirectResponse
from auth import build_oauth_url, exchange_code, get_discord_user, create_jwt, FRONTEND_URL
from deps import get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/login")
async def login():
    return {"url": build_oauth_url()}


# Discord가 직접 백엔드로 리다이렉트할 때 처리 (DISCORD_REDIRECT_URI = 백엔드 URL/auth/callback)
@router.get("/callback")
async def oauth_callback(code: str = Query(...), error: str = Query(None)):
    if error:
        return RedirectResponse(f"{FRONTEND_URL}/login?error={error}")
    try:
        token_data   = await exchange_code(code)
        access_token = token_data["access_token"]
        user         = await get_discord_user(access_token)
        jwt_token    = create_jwt(
            user_id=user["id"],
            username=user["username"],
            avatar=user.get("avatar", ""),
            access_token=access_token,
        )
        return RedirectResponse(f"{FRONTEND_URL}/callback?token={jwt_token}")
    except Exception as e:
        return RedirectResponse(f"{FRONTEND_URL}/login?error={str(e)}")


@router.post("/callback")
async def callback(body: dict):
    code = body.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="코드가 없습니다.")
    try:
        token_data   = await exchange_code(code)
        access_token = token_data["access_token"]
        user         = await get_discord_user(access_token)
        jwt_token    = create_jwt(
            user_id=user["id"],
            username=user["username"],
            avatar=user.get("avatar", ""),
            access_token=access_token,
        )
        return {"token": jwt_token, "user": user}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"인증 실패: {str(e)}")


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    avatar_url = (
        f"https://cdn.discordapp.com/avatars/{user['sub']}/{user['avatar']}.png"
        if user.get("avatar")
        else f"https://cdn.discordapp.com/embed/avatars/0.png"
    )
    return {
        "id":       user["sub"],
        "username": user["username"],
        "avatar":   avatar_url,
    }
