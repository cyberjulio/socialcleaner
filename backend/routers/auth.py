import random
import uuid
import logging

from fastapi import APIRouter, HTTPException, Request
from playwright.async_api import async_playwright

from backend.database import get_db
from backend.models import SessionCreate, SessionResponse
from backend.utils.crypto import encrypt_json
from backend.platforms.user_agents import USER_AGENTS
from backend.platforms.instagram import InstagramClient
from backend.platforms.twitter import TwitterClient

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/connect", response_model=SessionResponse)
async def connect_session(data: SessionCreate, request: Request):
    """Validate cookies and store an encrypted session."""
    if data.platform not in ("instagram", "twitter"):
        raise HTTPException(400, "Platform must be 'instagram' or 'twitter'")

    # Validate required cookies
    if data.platform == "instagram":
        required = {"sessionid", "csrftoken", "ds_user_id"}
    else:
        required = {"auth_token", "ct0"}

    missing = required - set(data.cookies.keys())
    if missing:
        raise HTTPException(400, f"Missing required cookies: {', '.join(missing)}")

    # Capture user-agent from the user's actual browser
    user_agent = request.headers.get("user-agent", "")
    if not user_agent:
        user_agent = random.choice(USER_AGENTS)
    logger.info(f"Captured user-agent: {user_agent}")

    # Launch a temporary browser to validate the session
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=user_agent,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )

        # Inject cookies
        domain = ".instagram.com" if data.platform == "instagram" else ".x.com"
        browser_cookies = []
        for name, value in data.cookies.items():
            browser_cookies.append({
                "name": name,
                "value": value,
                "domain": domain,
                "path": "/",
                "httpOnly": name not in ("csrftoken", "ct0"),
                "secure": True,
                "sameSite": "None",
            })
        await context.add_cookies(browser_cookies)

        # Validate
        if data.platform == "instagram":
            client = InstagramClient(context, data.cookies)
        else:
            client = TwitterClient(context, data.cookies)

        try:
            user_info = await client.validate_session()
        except Exception as e:
            logger.error(f"Session validation failed: {e}")
            raise HTTPException(401, "Session validation failed. Check that your cookies are correct and not expired.")

        username = user_info.get("username", "unknown")

        # Store encrypted session
        session_id = str(uuid.uuid4())
        cookies_enc = encrypt_json(data.cookies)

        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO sessions (id, platform, cookies_enc, user_agent, username) VALUES (?, ?, ?, ?, ?)",
                (session_id, data.platform, cookies_enc, user_agent, username),
            )
            await db.commit()
        finally:
            await db.close()

        await context.close()
        await browser.close()

        return SessionResponse(
            id=session_id,
            platform=data.platform,
            username=username,
            valid=True,
        )
    finally:
        await pw.stop()


@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions():
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT id, platform, username, valid FROM sessions ORDER BY created_at DESC"
        )
        return [
            SessionResponse(id=r["id"], platform=r["platform"], username=r["username"], valid=bool(r["valid"]))
            for r in rows
        ]
    finally:
        await db.close()


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    db = await get_db()
    try:
        await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()
