"""Shared display components for the CLI."""
import sys
import tty
import termios
from rich.console import Console
from rich.live import Live
from rich.panel import Panel

console = Console()


def read_key() -> str:
    """Read a single keypress without requiring Enter.

    Returns plain characters for normal keys.
    Returns 'UP' / 'DOWN' for arrow keys, 'ENTER' for Enter/Return.
    """
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch == "\x1b":
            seq = sys.stdin.read(2)
            if seq == "[A":
                return "UP"
            if seq == "[B":
                return "DOWN"
            return ch
        if ch in ("\r", "\n"):
            return "ENTER"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _build_menu_panel(title: str, options: list[str], selected: int, border_style: str) -> Panel:
    lines = []
    for i, option in enumerate(options):
        if i == selected:
            lines.append(f"  [bold cyan]> {i + 1}. {option}[/bold cyan]")
        else:
            lines.append(f"    {i + 1}. {option}")
    return Panel("\n".join(lines), title=title, border_style=border_style, padding=(1, 2))


def show_menu(title: str, options: list[str], border_style: str = "cyan") -> str | None:
    """Display a menu with arrow-key navigation and number-key selection.

    Returns the 1-based index as a string (e.g. "1", "2") to match callers.
    """
    selected = 0
    count = len(options)

    with Live(_build_menu_panel(title, options, selected, border_style),
              console=console, refresh_per_second=30, transient=False) as live:
        while True:
            key = read_key()

            if key == "UP":
                selected = (selected - 1) % count
            elif key == "DOWN":
                selected = (selected + 1) % count
            elif key == "ENTER":
                live.update(_build_menu_panel(title, options, selected, border_style))
                return str(selected + 1)
            elif key.isdigit() and 1 <= int(key) <= count:
                live.update(_build_menu_panel(title, options, int(key) - 1, border_style))
                return key
            else:
                continue

            live.update(_build_menu_panel(title, options, selected, border_style))


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
