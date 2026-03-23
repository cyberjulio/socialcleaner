"""Main CLI application — menu loop and signal handling."""
import sys

from cli.display import console, show_menu, show_panel, read_key

BANNER = r"""[bold cyan]
  ____             _       _  ____ _
 / ___|  ___   ___(_) __ _| |/ ___| | ___  __ _ _ __   ___ _ __
 \___ \ / _ \ / __| |/ _` | | |   | |/ _ \/ _` | '_ \ / _ \ '__|
  ___) | (_) | (__| | (_| | | |___| |  __/ (_| | | | |  __/ |
 |____/ \___/ \___|_|\__,_|_|\____|_|\___|\__,_|_| |_|\___|_|
[/bold cyan]"""


async def main():
    """Main CLI entry point."""
    from backend.database import init_db

    await init_db()

    while True:
        try:
            console.clear()
            console.print(BANNER)
            key = show_menu(
                "SocialCleaner",
                [
                    "[CLI] Unlike Instagram Posts",
                    "[CLI] Delete Instagram Comments",
                    "[CLI + WEB] Manage Accounts",
                    "[WEB] Start Web Dashboard",
                    "About",
                    "Quit",
                ],
            )

            if key == "1":
                from cli.tasks import run_task_flow

                await run_task_flow("likes")
            elif key == "2":
                from cli.tasks import run_task_flow

                await run_task_flow("comments")
            elif key == "3":
                from cli.auth import manage_accounts

                await manage_accounts()
            elif key == "4":
                await start_web_dashboard()
            elif key == "5":
                show_about()
            elif key == "6":
                console.print("\n  [cyan]Goodbye![/cyan]\n")
                sys.exit(0)

        except KeyboardInterrupt:
            console.print("\n  [cyan]Goodbye![/cyan]\n")
            sys.exit(0)


async def start_web_dashboard():
    """Launch uvicorn serving the full app (backend + frontend static files)."""
    import socket
    import subprocess
    import webbrowser
    import asyncio

    port = 8585

    # Check port availability
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            console.print(f"\n  [red]Port {port} is already in use.[/red]")
            console.print("  [dim]Stop the other process and try again.[/dim]")
            console.print("\n  Press any key to return to menu...")
            read_key()
            return

    console.print("\n  [cyan]Starting web dashboard...[/cyan]")

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    await asyncio.sleep(2)

    url = f"http://127.0.0.1:{port}"
    webbrowser.open(url)
    console.print(f"  [green]Web dashboard running at {url}[/green]")
    console.print("  [dim]Press Q to stop the server and return to menu[/dim]\n")

    try:
        while True:
            key = read_key().lower()
            if key == "q":
                break
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        console.print("  [cyan]Server stopped.[/cyan]")


def show_about():
    """Display the about screen."""
    about_text = (
        "[bold]SocialCleaner[/bold] v1.0\n\n"
        "Bulk-remove likes and comments from\n"
        "Instagram. Self-hosted and private —\n"
        "your data never leaves your machine.\n\n"
        "[dim]Created by: instagram.com/cyberjulio[/dim]\n"
        "[dim]GitHub: github.com/cyberjulio/socialcleaner[/dim]\n"
        "[dim]License: CC BY-NC 4.0[/dim]"
    )
    console.print()
    show_panel("About SocialCleaner", about_text, border_style="bright_blue")
    console.print("  [dim]Press any key to return to menu[/dim]")
    read_key()
