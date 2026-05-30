"""End-to-end: wizard --answers-file → generated yaml → validate.

Invokes the actual CLI via CliRunner; round-trips through file I/O.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from vastde_orch.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "answers_full.yaml"


class TestWizardRoundtrip:
    def test_wizard_writes_valid_yaml_then_validate_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Required env vars for the *generated* YAML's load_config step
        # (the wizard stores `${VMS_TOKEN}` placeholders that validate expands).
        monkeypatch.setenv("VMS_TOKEN", "fake")
        monkeypatch.setenv("REGISTRY_USER", "u")
        monkeypatch.setenv("REGISTRY_PASSWORD", "p")

        runner = CliRunner()
        out = tmp_path / "wiz.yaml"

        # 1. Run wizard with answers-file (CI-safe, non-TTY).
        wiz = runner.invoke(
            main,
            ["wizard", "--answers-file", str(FIXTURE), "-o", str(out)],
        )
        assert wiz.exit_code == 0, wiz.output
        assert out.exists()
        assert "Wrote" in wiz.output

        # 2. The written YAML parses.
        on_disk = yaml.safe_load(out.read_text())
        assert on_disk["vms"]["tenant"] == "data-platform"
        assert on_disk["enablement"]["event_broker"]["kind"] == "vast"
        assert len(on_disk["pipelines"]) == 1

        # 3. validate command accepts it.
        val = runner.invoke(main, ["validate", "-c", str(out)])
        assert val.exit_code == 0, val.output
        assert "OK" in val.output

    def test_wizard_without_answers_file_and_no_tty_exits_2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VASTDE_NO_INTERACTIVE", raising=False)
        # CliRunner stdin is not a TTY → require_tty should exit 2.
        runner = CliRunner()
        result = runner.invoke(main, ["wizard", "-o", str(tmp_path / "x.yaml")])
        assert result.exit_code == 2
        assert "interactive terminal" in result.output

    def test_wizard_backup_on_second_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VMS_TOKEN", "fake")
        monkeypatch.setenv("REGISTRY_USER", "u")
        monkeypatch.setenv("REGISTRY_PASSWORD", "p")

        runner = CliRunner()
        out = tmp_path / "wiz.yaml"
        runner.invoke(main, ["wizard", "--answers-file", str(FIXTURE), "-o", str(out)])
        second = runner.invoke(
            main, ["wizard", "--answers-file", str(FIXTURE), "-o", str(out)]
        )
        assert second.exit_code == 0
        assert (tmp_path / "wiz.yaml.bak.1").exists()
        assert "Backed up" in second.output
