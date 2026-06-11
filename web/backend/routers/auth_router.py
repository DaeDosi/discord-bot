from urllib.parse import quote
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import JSONResponse, RedirectResponse
from auth import build_oauth_url, exchange_code, get_discord_user, create_jwt, FRONTEND_URL, verify_oauth_state
from deps import get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/login")
async def login():
    return {"url": build_oauth_url()}


@router.get("/callback")
async def oauth_callback(
    code: str = Query(...),
    state: str = Query(None),
    error: str = Query(None),
):
    if error:
        return RedirectResponse(f"{FRONTEND_URL}/login?error={quote(error)}")
    if not verify_oauth_state(state):
        return RedirectResponse(f"{FRONTEND_URL}/login?error=invalid_state")
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
        return RedirectResponse(f"{FRONTEND_URL}/login?error={quote(str(e))}")


@router.post("/callback")
async def callback(body: dict):
    """프론트엔드가 직접 code를 교환하는 레거시 엔드포인트."""
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
        else "https://cdn.discordapp.com/embed/avatars/0.png"
    )
    return {
        "id":       user["sub"],
        "username": user["username"],
        "avatar":   avatar_url,
    }
