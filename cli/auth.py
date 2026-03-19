"""Account management — interactive browser login, list, remove."""
import uuid

import asyncio
from rich.table import Table
from rich.spinner import Spinner
from rich.live import Live

from cli.display import console, show_menu, read_key, confirm

CLI_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) "
    "Gecko/20100101 Firefox/137.0"
)


async def manage_accounts():
    """Account management submenu."""
    while True:
        key = show_menu(
            "Manage Accounts",
            [
                "Add Instagram Account",
                "View Connected Accounts",
                "Remove Account",
                "Back to Main Menu",
            ],
            border_style="green",
        )

        if key == "1":
            await add_instagram_account()
        elif key == "2":
            await view_accounts()
        elif key == "3":
            await remove_account()
        elif key == "4":
            return

        console.print()


async def add_instagram_account():
    """Open a visible Firefox browser for the user to log in to Instagram."""
    from playwright.async_api import async_playwright
    from backend.database import get_db
    from backend.utils.crypto import encrypt_json

    console.print("\n  [cyan]Opening Instagram login page...[/cyan]")
    console.print("  [dim]Log in normally in the browser window that opens.[/dim]")
    console.print("  [dim]The CLI will detect when you're logged in.[/dim]\n")

    pw = await async_playwright().start()
    browser = None
    try:
        browser = await pw.firefox.launch(headless=False)
        context = await browser.new_context(
            user_agent=CLI_USER_AGENT,
            viewport={"width": 1024, "height": 768},
            locale="en-US",
        )
        page = await context.new_page()
        await page.goto(
            "https://www.instagram.com/accounts/login/",
            wait_until="domcontentloaded",
        )

        # Poll for successful login (sessionid cookie appears)
        logged_in = False
        with Live(
            Spinner("dots", text="Waiting for login..."),
            console=console,
            refresh_per_second=4,
        ):
            for _ in range(300):  # 10 minute timeout
                await asyncio.sleep(2)
                cookies = await context.cookies("https://www.instagram.com")
                cookie_dict = {c["name"]: c["value"] for c in cookies}
                if "sessionid" in cookie_dict:
                    logged_in = True
                    break

        if not logged_in:
            console.print("  [red]Login timed out. Please try again.[/red]")
            return

        # Extract required cookies
        required = {"sessionid", "csrftoken", "ds_user_id"}
        missing = required - set(cookie_dict.keys())
        if missing:
            console.print(
                f"  [red]Missing cookies: {', '.join(missing)}. Try again.[/red]"
            )
            return

        ig_cookies = {k: cookie_dict[k] for k in required}

        # Validate session
        console.print("  [cyan]Validating session...[/cyan]")
        from backend.platforms.instagram import InstagramClient

        client = InstagramClient(context, ig_cookies)
        try:
            user_info = await client.validate_session()
            username = user_info.get("username", "unknown")
        except Exception:
            console.print(
                "  [red]Login detected but session couldn't be verified.[/red]"
            )
            if confirm("Try again?"):
                await client.close()
                await browser.close()
                await pw.stop()
                return await add_instagram_account()
            return
        finally:
            await client.close()

        # Store in database
        session_id = str(uuid.uuid4())
        cookies_enc = encrypt_json(ig_cookies)

        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO sessions (id, platform, cookies_enc, user_agent, username) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, "instagram", cookies_enc, CLI_USER_AGENT, username),
            )
            await db.commit()
        finally:
            await db.close()

        console.print(
            f"\n  [green]Account @{username} connected successfully![/green]"
        )

    except KeyboardInterrupt:
        console.print("\n  [yellow]Login cancelled.[/yellow]")
    except Exception as e:
        console.print(f"\n  [red]Error: {e}[/red]")
    finally:
        if browser:
            await browser.close()
        await pw.stop()


async def view_accounts():
    """Display connected accounts in a rich table."""
    from backend.database import get_db

    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT id, platform, username, valid, created_at "
            "FROM sessions ORDER BY created_at DESC"
        )
    finally:
        await db.close()

    if not rows:
        console.print("\n  [yellow]No accounts connected yet.[/yellow]")
        console.print("  [dim]Use 'Add Instagram Account' to get started.[/dim]")
        return

    table = Table(title="Connected Accounts", border_style="green")
    table.add_column("#", style="dim", width=3)
    table.add_column("Platform", style="cyan")
    table.add_column("Username", style="bold")
    table.add_column("Status")
    table.add_column("Added", style="dim")

    for i, row in enumerate(rows, 1):
        status = "[green]Active[/green]" if row["valid"] else "[red]Invalid[/red]"
        added = row["created_at"][:10] if row["created_at"] else "—"
        table.add_row(
            str(i), row["platform"].title(), f"@{row['username']}", status, added
        )

    console.print()
    console.print(table)


async def remove_account():
    """Remove a connected account."""
    from backend.database import get_db

    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT id, platform, username FROM sessions ORDER BY created_at DESC"
        )
    finally:
        await db.close()

    if not rows:
        console.print("\n  [yellow]No accounts to remove.[/yellow]")
        return

    console.print("\n  [bold]Select account to remove:[/bold]\n")
    for i, row in enumerate(rows, 1):
        console.print(f"    {i}. {row['platform'].title()} — @{row['username']}")
    console.print(f"    {len(rows) + 1}. Cancel")
    console.print()

    key = read_key()
    try:
        idx = int(key) - 1
    except ValueError:
        return

    if idx < 0 or idx >= len(rows):
        return

    row = rows[idx]
    if not confirm(f"Remove @{row['username']}?"):
        console.print("  [dim]Cancelled.[/dim]")
        return

    db = await get_db()
    try:
        await db.execute("DELETE FROM sessions WHERE id = ?", (row["id"],))
        await db.commit()
    finally:
        await db.close()

    console.print(f"  [green]Account @{row['username']} removed.[/green]")


async def select_account(platform: str = "instagram") -> dict | None:
    """Select an account for a task. Returns session row dict or None."""
    from backend.database import get_db

    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT * FROM sessions WHERE platform = ? AND valid = 1 "
            "ORDER BY created_at DESC",
            (platform,),
        )
    finally:
        await db.close()

    if not rows:
        console.print("\n  [yellow]No Instagram accounts connected.[/yellow]")
        if confirm("Add one now?"):
            await add_instagram_account()
            return await select_account(platform)
        return None

    if len(rows) == 1:
        console.print(f"\n  Using account [bold]@{rows[0]['username']}[/bold]")
        return dict(rows[0])

    console.print("\n  [bold]Select account:[/bold]\n")
    for i, row in enumerate(rows, 1):
        console.print(f"    {i}. @{row['username']}")
    console.print()

    key = read_key()
    try:
        idx = int(key) - 1
    except ValueError:
        return None

    if 0 <= idx < len(rows):
        return dict(rows[idx])
    return None
