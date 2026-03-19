"""Shared display components for the CLI."""
import sys
import tty
import termios
from rich.console import Console
from rich.panel import Panel

console = Console()


def read_key() -> str:
    """Read a single keypress without requiring Enter."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x03":
            raise KeyboardInterrupt
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def show_menu(title: str, options: list[str], border_style: str = "cyan") -> str | None:
    """Display a numbered menu and return the key pressed."""
    lines = []
    for i, option in enumerate(options, 1):
        lines.append(f"  {i}. {option}")

    content = "\n".join(lines)
    panel = Panel(content, title=title, border_style=border_style, padding=(1, 2))
    console.print(panel)
    console.print("  [dim]Press a number to select[/dim]\n")

    key = read_key()
    return key


def show_panel(title: str, content: str, border_style: str = "cyan"):
    """Display a simple panel."""
    panel = Panel(content, title=title, border_style=border_style, padding=(1, 2))
    console.print(panel)


def confirm(message: str) -> bool:
    """Ask for Y/N confirmation with single keypress."""
    console.print(f"\n  {message} [cyan](Y/N)[/cyan] ", end="")
    key = read_key().lower()
    console.print(key)
    return key == "y"
