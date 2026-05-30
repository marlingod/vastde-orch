"""Tests for src/vastde_orch/interactive/_tty.py."""

from __future__ import annotations

import io
import sys
from unittest.mock import MagicMock

import click
import pytest
from pytest_mock import MockerFixture

from vastde_orch.interactive._tty import (
    ENV_NO_INTERACTIVE,
    color_enabled,
    is_non_interactive_env,
    require_tty,
)


class TestNonInteractiveEnv:
    def test_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ENV_NO_INTERACTIVE, raising=False)
        assert is_non_interactive_env() is False

    def test_set_truthy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_NO_INTERACTIVE, "1")
        assert is_non_interactive_env() is True

    def test_set_whitespace_only_is_falsy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_NO_INTERACTIVE, "   ")
        assert is_non_interactive_env() is False


class TestRequireTty:
    def test_tty_allows_interactive(self, mocker: MockerFixture) -> None:
        mocker.patch("vastde_orch.interactive._tty.sys.stdin.isatty", return_value=True)
        # Should return without raising or exiting.
        require_tty(None, command="wizard", ci_hint="use --answers-file")

    def test_non_tty_without_opt_out_exits_2(
        self, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(ENV_NO_INTERACTIVE, raising=False)
        mocker.patch("vastde_orch.interactive._tty.sys.stdin.isatty", return_value=False)
        ctx = MagicMock(spec=click.Context)
        ctx.exit.side_effect = SystemExit(2)

        with pytest.raises(SystemExit) as exc:
            require_tty(ctx, command="wizard", ci_hint="vastde-orch wizard --answers-file x.yaml")
        assert exc.value.code == 2
        ctx.exit.assert_called_once_with(2)

    def test_non_tty_with_env_opt_out_returns(
        self, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_NO_INTERACTIVE, "1")
        mocker.patch("vastde_orch.interactive._tty.sys.stdin.isatty", return_value=False)
        # Should NOT exit.
        require_tty(None, command="apply", ci_hint="use --yes-all")

    def test_non_tty_with_flag_opt_out_returns(self, mocker: MockerFixture) -> None:
        mocker.patch("vastde_orch.interactive._tty.sys.stdin.isatty", return_value=False)
        require_tty(None, command="apply", ci_hint="use --yes-all", non_interactive_flag=True)

    def test_error_message_includes_ci_hint(
        self, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.delenv(ENV_NO_INTERACTIVE, raising=False)
        mocker.patch("vastde_orch.interactive._tty.sys.stdin.isatty", return_value=False)
        ctx = MagicMock(spec=click.Context)
        ctx.exit.side_effect = SystemExit(2)
        with pytest.raises(SystemExit):
            require_tty(ctx, command="wizard", ci_hint="vastde-orch wizard --answers-file x.yaml")
        err = capsys.readouterr().err
        assert "wizard" in err
        assert "--answers-file" in err
        assert ENV_NO_INTERACTIVE in err


class TestColorEnabled:
    def test_tty_default_enabled(self, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("TERM", "xterm-256color")
        stream = mocker.MagicMock()
        stream.isatty.return_value = True
        assert color_enabled(stream) is True

    def test_no_color_disables(self, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NO_COLOR", "1")
        stream = mocker.MagicMock()
        stream.isatty.return_value = True
        assert color_enabled(stream) is False

    def test_term_dumb_disables(self, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("TERM", "dumb")
        stream = mocker.MagicMock()
        stream.isatty.return_value = True
        assert color_enabled(stream) is False

    def test_non_tty_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("TERM", "xterm")
        assert color_enabled(io.StringIO()) is False

    def test_no_color_set_whitespace_only_does_not_disable(
        self, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # NO_COLOR convention: any non-empty value disables; whitespace-only is empty.
        monkeypatch.setenv("NO_COLOR", "   ")
        monkeypatch.setenv("TERM", "xterm")
        stream = mocker.MagicMock()
        stream.isatty.return_value = True
        assert color_enabled(stream) is True
