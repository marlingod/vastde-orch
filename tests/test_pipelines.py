"""Tests for src/vastde_orch/pipelines/*."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from vastde_orch.config.models import (
    DeploymentSpec,
    ElementTriggerSpec,
    FlowEdge,
    FunctionSpec,
    MinMax,
    MinMaxStr,
    PipelineSpec,
    ScheduleSpec,
    ScheduleTriggerSpec,
)
from vastde_orch.pipelines.functions import compute_image_tag, ensure_function
from vastde_orch.pipelines.pipelines import ensure_pipeline, _function_deployment_body
from vastde_orch.pipelines.triggers import ensure_trigger


# ── functions.py ────────────────────────────────────────────────────────────

class TestFunctionsHash:
    def test_explicit_tag_wins(self, tmp_path: Path) -> None:
        spec = FunctionSpec(name="f", source=tmp_path, image="r/f", tag="v1.0")
        assert compute_image_tag(spec) == "v1.0"

    def test_content_hash_stable(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("print('hi')\n")
        (tmp_path / "requirements.txt").write_text("requests==2.31\n")
        spec = FunctionSpec(name="f", source=tmp_path, image="r/f")
        h1 = compute_image_tag(spec)
        h2 = compute_image_tag(spec)
        assert h1 == h2
        assert len(h1) == 12

    def test_content_hash_changes_when_source_changes(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("v1\n")
        spec = FunctionSpec(name="f", source=tmp_path, image="r/f")
        h1 = compute_image_tag(spec)
        (tmp_path / "main.py").write_text("v2\n")
        h2 = compute_image_tag(spec)
        assert h1 != h2

    def test_missing_source_raises(self, tmp_path: Path) -> None:
        spec = FunctionSpec(name="f", source=tmp_path / "nope", image="r/f")
        with pytest.raises(FileNotFoundError):
            compute_image_tag(spec)


class TestEnsureFunction:
    @pytest.fixture
    def src(self, tmp_path: Path) -> Path:
        (tmp_path / "main.py").write_text("v1\n")
        return tmp_path

    def test_creates_when_absent_and_image_missing(
        self, src: Path, mocker: MockerFixture
    ) -> None:
        cli = MagicMock()
        cli.functions_get.return_value = None
        mocker.patch("vastde_orch.pipelines.functions.docker_manifest_exists", return_value=False)
        mocker.patch("vastde_orch.pipelines.functions.docker_tag")
        mocker.patch("vastde_orch.pipelines.functions.docker_push")

        spec = FunctionSpec(name="f1", source=src, image="r/f1")
        result = ensure_function(cli, spec)

        assert result.de_resource_status == "created"
        assert not result.image_already_in_registry
        cli.functions_build.assert_called_once()
        cli.functions_create.assert_called_once()

    def test_skips_build_when_image_in_registry(
        self, src: Path, mocker: MockerFixture
    ) -> None:
        cli = MagicMock()
        cli.functions_get.return_value = None
        mocker.patch("vastde_orch.pipelines.functions.docker_manifest_exists", return_value=True)
        push = mocker.patch("vastde_orch.pipelines.functions.docker_push")

        spec = FunctionSpec(name="f1", source=src, image="r/f1")
        result = ensure_function(cli, spec)

        assert result.image_already_in_registry
        cli.functions_build.assert_not_called()
        push.assert_not_called()
        cli.functions_create.assert_called_once()

    def test_updates_when_image_drift(
        self, src: Path, mocker: MockerFixture
    ) -> None:
        cli = MagicMock()
        cli.functions_get.return_value = {
            "name": "f1", "container_image": "r/f1:OLD"
        }
        mocker.patch("vastde_orch.pipelines.functions.docker_manifest_exists", return_value=True)

        spec = FunctionSpec(name="f1", source=src, image="r/f1")
        result = ensure_function(cli, spec)

        assert result.de_resource_status == "updated"
        cli.functions_new_revision.assert_called_once()
        cli.functions_create.assert_not_called()

    def test_unchanged_when_same(self, src: Path, mocker: MockerFixture) -> None:
        cli = MagicMock()
        spec = FunctionSpec(name="f1", source=src, image="r/f1")
        # Hash matches what compute_image_tag will return
        tag = compute_image_tag(spec)
        cli.functions_get.return_value = {
            "name": "f1", "container_image": f"r/f1:{tag}"
        }
        mocker.patch("vastde_orch.pipelines.functions.docker_manifest_exists", return_value=True)

        result = ensure_function(cli, spec)

        assert result.de_resource_status == "unchanged"
        cli.functions_create.assert_not_called()
        cli.functions_new_revision.assert_not_called()

    def test_dry_run_no_writes(self, src: Path, mocker: MockerFixture) -> None:
        cli = MagicMock()
        cli.functions_get.return_value = None
        mocker.patch("vastde_orch.pipelines.functions.docker_manifest_exists", return_value=False)
        push = mocker.patch("vastde_orch.pipelines.functions.docker_push")

        spec = FunctionSpec(name="f1", source=src, image="r/f1")
        result = ensure_function(cli, spec, dry_run=True)

        assert result.de_resource_status == "would_create"
        cli.functions_build.assert_not_called()
        push.assert_not_called()
        cli.functions_create.assert_not_called()


# ── triggers.py ─────────────────────────────────────────────────────────────

class TestEnsureTrigger:
    def test_creates_element_trigger(self) -> None:
        cli = MagicMock()
        cli.triggers_get.return_value = None
        spec = ElementTriggerSpec(
            name="t1", type="element", source_view="/raw",
            event_type="ElementCreated", topic="default",
        )
        result = ensure_trigger(cli, spec, broker_view_id=42)
        assert result.status == "created"
        body = cli.triggers_create.call_args.args[1]
        assert body["target_kafka_view_id"] == 42
        assert body["source_view"] == "/raw"

    def test_schedule_trigger_simple(self) -> None:
        cli = MagicMock()
        cli.triggers_get.return_value = None
        spec = ScheduleTriggerSpec(
            name="t1", type="schedule",
            schedule=ScheduleSpec(simple="0 2 * * *"),
            topic="default",
        )
        result = ensure_trigger(cli, spec)
        assert result.status == "created"
        body = cli.triggers_create.call_args.args[1]
        assert body["schedule"] == {"mode": "simple", "expression": "0 2 * * *"}

    def test_unchanged_when_no_drift(self) -> None:
        spec = ElementTriggerSpec(
            name="t1", type="element", source_view="/raw",
            event_type="ElementCreated", topic="default",
        )
        from vastde_orch.pipelines.triggers import _to_body
        body = _to_body(spec, None)
        cli = MagicMock()
        cli.triggers_get.return_value = body
        result = ensure_trigger(cli, spec)
        assert result.status == "unchanged"

    def test_dry_run(self) -> None:
        cli = MagicMock()
        cli.triggers_get.return_value = None
        spec = ElementTriggerSpec(
            name="t1", type="element", source_view="/raw",
            event_type="ElementCreated", topic="default",
        )
        result = ensure_trigger(cli, spec, dry_run=True)
        assert result.status == "would_create"
        cli.triggers_create.assert_not_called()


# ── pipelines.py ────────────────────────────────────────────────────────────

class TestEnsurePipeline:
    @pytest.fixture
    def src(self, tmp_path: Path) -> Path:
        (tmp_path / "main.py").write_text("hi\n")
        return tmp_path

    def test_creates_full_pipeline_and_deploys(
        self, src: Path, mocker: MockerFixture
    ) -> None:
        cli = MagicMock()
        cli.triggers_get.return_value = None
        cli.functions_get.return_value = None
        cli.pipelines_get.return_value = None
        mocker.patch("vastde_orch.pipelines.functions.docker_manifest_exists", return_value=True)

        spec = PipelineSpec(
            name="p1", k8s_cluster="k",
            triggers=[ElementTriggerSpec(
                name="t1", type="element", source_view="/raw",
                event_type="ElementCreated", topic="default",
            )],
            functions=[FunctionSpec(name="f1", source=src, image="r/f1")],
            flow=[FlowEdge.model_validate({"from": "t1", "to": "f1"})],
        )

        result = ensure_pipeline(cli, spec)

        cli.triggers_create.assert_called_once()
        cli.functions_create.assert_called_once()
        cli.pipelines_create.assert_called_once()
        cli.pipelines_deploy.assert_called_once_with("p1")
        assert result.status == "deployed"

    def test_dry_run_does_not_deploy(self, src: Path, mocker: MockerFixture) -> None:
        cli = MagicMock()
        cli.triggers_get.return_value = None
        cli.functions_get.return_value = None
        cli.pipelines_get.return_value = None
        mocker.patch("vastde_orch.pipelines.functions.docker_manifest_exists", return_value=True)

        spec = PipelineSpec(
            name="p1", k8s_cluster="k",
            functions=[FunctionSpec(name="f1", source=src, image="r/f1")],
        )

        ensure_pipeline(cli, spec, dry_run=True)
        cli.pipelines_create.assert_not_called()
        cli.pipelines_deploy.assert_not_called()

    def test_function_deployment_body_carries_all_fields(self, src: Path) -> None:
        f = FunctionSpec(
            name="f", source=src, image="r/f",
            deployment=DeploymentSpec(
                cpu=MinMax(min=0.5, max=2),
                memory=MinMaxStr(min="512Mi", max="2Gi"),
                timeout_seconds=120,
                retries=2,
                log_level="DEBUG",
                method_of_delivery="unordered",
            ),
            secrets={"s3": {"k": "v"}},
            env={"FOO": "bar"},
        )
        body = _function_deployment_body(f)
        assert body["cpu"] == {"min": 0.5, "max": 2.0}
        assert body["memory"] == {"min": "512Mi", "max": "2Gi"}
        assert body["timeout_seconds"] == 120
        assert body["log_level"] == "DEBUG"
        assert body["method_of_delivery"] == "unordered"
        assert body["secrets"] == {"s3": {"k": "v"}}
        assert body["env"] == {"FOO": "bar"}
