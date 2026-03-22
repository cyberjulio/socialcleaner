"""Tests that page.evaluate calls don't use f-string interpolation of cookie values."""

import ast
import inspect
import textwrap

import pytest


def _get_evaluate_calls(source: str) -> list[ast.Call]:
    """Find all page.evaluate(...) calls in source code."""
    tree = ast.parse(source)
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "evaluate"
                and isinstance(func.value, ast.Name)
                and func.value.id == "page"
            ):
                calls.append(node)
    return calls


def _has_fstring_arg(call: ast.Call) -> bool:
    """Check if the first argument to a call is a JoinedStr (f-string)."""
    if call.args:
        return isinstance(call.args[0], ast.JoinedStr)
    return False


class TestNoFStringEvaluate:
    """page.evaluate must not use f-strings — use Playwright arg passing instead."""

    def test_instagram_no_fstring_evaluate(self):
        from backend.platforms import instagram

        source = inspect.getsource(instagram)
        calls = _get_evaluate_calls(source)
        assert len(calls) > 0, "Expected at least one page.evaluate call"
        fstring_calls = [c for c in calls if _has_fstring_arg(c)]
        assert len(fstring_calls) == 0, (
            f"Found {len(fstring_calls)} page.evaluate(f'...') call(s) in instagram.py — "
            "use Playwright argument passing instead"
        )

    def test_twitter_no_fstring_evaluate(self):
        from backend.platforms import twitter

        source = inspect.getsource(twitter)
        calls = _get_evaluate_calls(source)
        assert len(calls) > 0, "Expected at least one page.evaluate call"
        fstring_calls = [c for c in calls if _has_fstring_arg(c)]
        assert len(fstring_calls) == 0, (
            f"Found {len(fstring_calls)} page.evaluate(f'...') call(s) in twitter.py — "
            "use Playwright argument passing instead"
        )
