"""Tests for src/vastde_orch/interactive/_yaml_emit.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from pytest_mock import MockerFixture

from vastde_orch.interactive._yaml_emit import (
    backup_existing,
    dump_yaml,
    write_atomic,
    write_yaml_with_backup,
)


class TestDumpYaml:
    def test_preserves_insertion_order(self) -> None:
        data = {"vms": {"address": "x"}, "enablement": {}, "pipelines": []}
        out = dump_yaml(data)
        assert out.index("vms:") < out.index("enablement:") < out.index("pipelines:")

    def test_block_style_not_flow(self) -> None:
        out = dump_yaml({"a": [1, 2, 3]})
        assert "- 1" in out
        assert "[1, 2, 3]" not in out


class TestWriteAtomic:
    def test_creates_file(self, tmp_path: Path) -> None:
        target = tmp_path / "out.yaml"
        write_atomic(target, "hello\n")
        assert target.read_text() == "hello\n"

    def test_creates_missing_parent_dir(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "deep" / "out.yaml"
        write_atomic(target, "ok\n")
        assert target.exists()

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        target = tmp_path / "out.yaml"
        target.write_text("old\n")
        write_atomic(target, "new\n")
        assert target.read_text() == "new\n"

    def test_failure_does_not_corrupt_existing_file(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        target = tmp_path / "out.yaml"
        target.write_text("original\n")
        # Simulate os.replace crashing mid-write.
        mocker.patch("vastde_orch.interactive._yaml_emit.os.replace", side_effect=OSError("boom"))
        with pytest.raises(OSError, match="boom"):
            write_atomic(target, "new\n")
        # Original file is intact.
        assert target.read_text() == "original\n"
        # No leftover temp files in the directory.
        tmp_files = [p for p in tmp_path.iterdir() if p.name.startswith(".out.yaml.")]
        assert tmp_files == []


class TestBackupExisting:
    def test_returns_none_when_target_missing(self, tmp_path: Path) -> None:
        assert backup_existing(tmp_path / "missing.yaml") is None

    def test_creates_bak_1(self, tmp_path: Path) -> None:
        target = tmp_path / "vastde.yaml"
        target.write_text("v1\n")
        bak = backup_existing(target)
        assert bak == tmp_path / "vastde.yaml.bak.1"
        assert bak.read_text() == "v1\n"
        assert not target.exists()

    def test_rotates_existing_backups(self, tmp_path: Path) -> None:
        target = tmp_path / "x.yaml"
        target.write_text("v3\n")
        (tmp_path / "x.yaml.bak.1").write_text("v2\n")
        (tmp_path / "x.yaml.bak.2").write_text("v1\n")

        backup_existing(target)

        assert (tmp_path / "x.yaml.bak.1").read_text() == "v3\n"
        assert (tmp_path / "x.yaml.bak.2").read_text() == "v2\n"
        assert (tmp_path / "x.yaml.bak.3").read_text() == "v1\n"
        assert not target.exists()

    def test_caps_at_keep_count(self, tmp_path: Path) -> None:
        target = tmp_path / "x.yaml"
        target.write_text("v4\n")
        (tmp_path / "x.yaml.bak.1").write_text("v3\n")
        (tmp_path / "x.yaml.bak.2").write_text("v2\n")
        (tmp_path / "x.yaml.bak.3").write_text("v1\n")  # would-be oldest

        backup_existing(target, keep=3)

        # bak.3 should now hold v2 (what was bak.2); v1 was dropped.
        assert (tmp_path / "x.yaml.bak.1").read_text() == "v4\n"
        assert (tmp_path / "x.yaml.bak.2").read_text() == "v3\n"
        assert (tmp_path / "x.yaml.bak.3").read_text() == "v2\n"
        assert not (tmp_path / "x.yaml.bak.4").exists()


class TestWriteYamlWithBackup:
    def test_first_write_no_backup(self, tmp_path: Path) -> None:
        target = tmp_path / "vastde.yaml"
        bak = write_yaml_with_backup(target, {"a": 1})
        assert bak is None
        assert yaml.safe_load(target.read_text()) == {"a": 1}

    def test_second_write_creates_backup(self, tmp_path: Path) -> None:
        target = tmp_path / "vastde.yaml"
        write_yaml_with_backup(target, {"a": 1})
        bak = write_yaml_with_backup(target, {"a": 2})
        assert bak == tmp_path / "vastde.yaml.bak.1"
        assert yaml.safe_load(bak.read_text()) == {"a": 1}
        assert yaml.safe_load(target.read_text()) == {"a": 2}
