import asyncio
import random
import uuid
import logging

from fastapi import APIRouter, HTTPException, Request
from playwright.async_api import async_playwright

from backend.database import get_db
from backend.models import SessionCreate, SessionResponse, BrowserLoginRequest, BrowserLoginStatus
from backend.utils.crypto import encrypt_json
from backend.platforms.user_agents import USER_AGENTS
from backend.platforms.instagram import InstagramClient
from backend.platforms.twitter import TwitterClient

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

# In-memory store for active browser login sessions
_browser_logins: dict[str, BrowserLoginStatus] = {}

BROWSER_LOGIN_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:148.0) "
    "Gecko/20100101 Firefox/148.0"
)


@router.post("/browser-login")
async def start_browser_login(data: BrowserLoginRequest):
    """Launch a visible browser for the user to log in. Returns a login_id to poll."""
    if data.platform not in ("instagram", "twitter"):
        raise HTTPException(400, "Platform must be 'instagram' or 'twitter'")

    login_id = str(uuid.uuid4())
    _browser_logins[login_id] = BrowserLoginStatus(
        login_id=login_id, status="waiting"
    )

    # Run the browser login flow in the background
    asyncio.create_task(_browser_login_flow(login_id, data.platform))

    return {"login_id": login_id}


@router.get("/browser-login/{login_id}/status", response_model=BrowserLoginStatus)
async def get_browser_login_status(login_id: str):
    """Poll the status of a browser login session."""
    status = _browser_logins.get(login_id)
    if not status:
        raise HTTPException(404, "Login session not found")
    return status


async def _browser_login_flow(login_id: str, platform: str):
    """Background task: open visible browser, wait for login, save session."""
    pw = await async_playwright().start()
    browser = None
    try:
        browser = await pw.firefox.launch(headless=False)
        context = await browser.new_context(
            user_agent=BROWSER_LOGIN_UA,
            viewport={"width": 1024, "height": 768},
            locale="en-US",
        )
        page = await context.new_page()

        if platform == "instagram":
            await page.goto(
                "https://www.instagram.com/accounts/login/",
                wait_until="domcontentloaded",
            )
            required_cookies = {"sessionid", "csrftoken", "ds_user_id"}
            domain = ".instagram.com"
            cookie_url = "https://www.instagram.com"
        else:
            await page.goto(
                "https://x.com/i/flow/login",
                wait_until="domcontentloaded",
            )
            required_cookies = {"auth_token", "ct0"}
            domain = ".x.com"
            cookie_url = "https://x.com"

        # Poll for successful login (required cookies appear)
        cookie_dict = {}
        logged_in = False
        for _ in range(150):  # 5 minute timeout (150 × 2s)
            if login_id not in _browser_logins:
                return  # cancelled
            await asyncio.sleep(2)
            cookies = await context.cookies(cookie_url)
            cookie_dict = {c["name"]: c["value"] for c in cookies}
            if required_cookies.issubset(cookie_dict.keys()):
                logged_in = True
                break

        if not logged_in:
            _browser_logins[login_id] = BrowserLoginStatus(
                login_id=login_id, status="timeout",
                error="Login timed out after 5 minutes",
            )
            return

        # Extract only the required cookies
        ig_cookies = {k: cookie_dict[k] for k in required_cookies}

        # Validate session
        if platform == "instagram":
            client = InstagramClient(context, ig_cookies)
        else:
            client = TwitterClient(context, ig_cookies)

        try:
            user_info = await client.validate_session()
            username = user_info.get("username", "unknown")
        except Exception as e:
            logger.error(f"Browser login validation failed: {e}")
            _browser_logins[login_id] = BrowserLoginStatus(
                login_id=login_id, status="error",
                error="Login detected but session validation failed",
            )
            return
        finally:
            await client.close()

        # Store encrypted session (replace existing for same platform+username)
        session_id = str(uuid.uuid4())
        cookies_enc = encrypt_json(ig_cookies)

        db = await get_db()
        try:
            await db.execute(
                "DELETE FROM sessions WHERE platform = ? AND username = ?",
                (platform, username),
            )
            await db.execute(
                "INSERT INTO sessions (id, platform, cookies_enc, user_agent, username) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, platform, cookies_enc, BROWSER_LOGIN_UA, username),
            )
            await db.commit()
        finally:
            await db.close()

        _browser_logins[login_id] = BrowserLoginStatus(
            login_id=login_id, status="success",
            username=username, session_id=session_id,
        )
        logger.info(f"Browser login success: @{username}")

    except Exception as e:
        logger.error(f"Browser login error: {e}", exc_info=True)
        _browser_logins[login_id] = BrowserLoginStatus(
            login_id=login_id, status="error", error=str(e),
        )
    finally:
        if browser:
            await browser.close()
        await pw.stop()


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

        # Store encrypted session (replace existing for same platform+username)
        session_id = str(uuid.uuid4())
        cookies_enc = encrypt_json(data.cookies)

        db = await get_db()
        try:
            await db.execute(
                "DELETE FROM sessions WHERE platform = ? AND username = ?",
                (data.platform, username),
            )
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
        # Delete dependent records before the session (foreign key constraints)
        task_rows = await db.execute_fetchall(
            "SELECT id FROM tasks WHERE session_id = ?", (session_id,)
        )
        for row in task_rows:
            task_id = row["id"]
            await db.execute("DELETE FROM events WHERE task_id = ?", (task_id,))
            await db.execute("DELETE FROM items WHERE task_id = ?", (task_id,))
        await db.execute("DELETE FROM tasks WHERE session_id = ?", (session_id,))
        await db.execute("DELETE FROM daily_actions WHERE session_id = ?", (session_id,))
        await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()
