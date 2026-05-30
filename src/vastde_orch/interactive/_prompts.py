"""Thin questionary adapter.

Every wizard prompt goes through `Prompter`. In production, methods delegate
to questionary. In tests, instantiate `Prompter(answers={...})` to bypass
questionary entirely (inject-answers pattern — see docs/research-interactive-ux.md §8).

Keys are dot-paths (e.g. "vms.address", "pipelines.0.name") so a single answers
dict can pre-fill the entire wizard.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import questionary


class PromptCancelled(RuntimeError):
    """Raised when the user hits Ctrl-C / Ctrl-D mid-prompt."""


def _walk(answers: dict[str, Any], key: str) -> Any:
    """Look up a dot-path key in a nested dict; raise KeyError if not found."""
    cur: Any = answers
    for part in key.split("."):
        if part.isdigit():
            cur = cur[int(part)]
        else:
            cur = cur[part]
    return cur


class Prompter:
    """Inject-answers-friendly wrapper around questionary.

    In production, instantiate with no args and call .text(...), .choice(...), etc.
    In tests, instantiate with `answers={"key": value, ...}` and the same calls
    will return values from that dict instead of prompting.
    """

    def __init__(self, answers: dict[str, Any] | None = None) -> None:
        self._answers = answers

    @property
    def is_scripted(self) -> bool:
        return self._answers is not None

    def _scripted_value(self, key: str, default: Any) -> Any:
        try:
            return _walk(self._answers, key)  # type: ignore[arg-type]
        except (KeyError, IndexError, TypeError):
            if default is not None:
                return default
            raise KeyError(f"answers file missing key: {key!r}") from None

    # ── primitives ─────────────────────────────────────────────────────

    def text(
        self,
        key: str,
        message: str,
        *,
        default: str | None = None,
        validate: Callable[[str], bool | str] | None = None,
    ) -> str:
        if self.is_scripted:
            return str(self._scripted_value(key, default))
        result = questionary.text(message, default=default or "", validate=validate).ask()
        if result is None:
            raise PromptCancelled
        return result

    def password(self, key: str, message: str) -> str:
        if self.is_scripted:
            return str(self._scripted_value(key, ""))
        result = questionary.password(message).ask()
        if result is None:
            raise PromptCancelled
        return result

    def confirm(self, key: str, message: str, *, default: bool = False) -> bool:
        if self.is_scripted:
            return bool(self._scripted_value(key, default))
        result = questionary.confirm(message, default=default).ask()
        if result is None:
            raise PromptCancelled
        return result

    def choice(
        self,
        key: str,
        message: str,
        choices: list[str],
        *,
        default: str | None = None,
    ) -> str:
        if self.is_scripted:
            value = str(self._scripted_value(key, default))
            if value not in choices:
                raise ValueError(
                    f"answers[{key!r}]={value!r} not one of {choices}"
                )
            return value
        result = questionary.select(message, choices=choices, default=default).ask()
        if result is None:
            raise PromptCancelled
        return result

    def integer(
        self,
        key: str,
        message: str,
        *,
        default: int | None = None,
        minimum: int | None = None,
    ) -> int:
        def _validate(s: str) -> bool | str:
            try:
                n = int(s)
            except ValueError:
                return "must be an integer"
            if minimum is not None and n < minimum:
                return f"must be >= {minimum}"
            return True

        raw = self.text(
            key, message, default=str(default) if default is not None else None, validate=_validate
        )
        return int(raw)

    # ── loops (e.g. "add another user?") ───────────────────────────────

    def loop(
        self,
        key: str,
        builder: Callable[[int, "Prompter"], dict[str, Any]],
        *,
        add_message: str = "Add another?",
    ) -> list[dict[str, Any]]:
        """Repeatedly ask `builder` to produce one item until the user declines.

        In scripted mode, `answers[key]` must be a list; `builder` is called
        once per element with a sub-Prompter scoped to that element.
        """
        items: list[dict[str, Any]] = []
        if self.is_scripted:
            scripted_list = self._scripted_value(key, [])
            if not isinstance(scripted_list, list):
                raise TypeError(f"answers[{key!r}] must be a list, got {type(scripted_list).__name__}")
            for i, item_answers in enumerate(scripted_list):
                sub = Prompter(answers=item_answers)
                items.append(builder(i, sub))
            return items

        i = 0
        while True:
            msg = "Add the first one?" if i == 0 else add_message
            if not questionary.confirm(msg, default=(i == 0)).ask():
                break
            items.append(builder(i, self))
            i += 1
        return items
