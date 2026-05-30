"""Trigger reconciliation via the `vastde` CLI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from vastde_orch.clients.vastde_cli import VastdeCli
from vastde_orch.config.models import (
    ElementTriggerSpec,
    ScheduleTriggerSpec,
    TriggerSpec,
)

TriggerStatus = Literal["created", "updated", "unchanged", "would_create", "would_update"]


@dataclass
class TriggerResult:
    name: str
    status: TriggerStatus


def _to_body(spec: TriggerSpec, broker_view_id: int | None) -> dict[str, Any]:
    common: dict[str, Any] = {
        "name": spec.name,
        "type": spec.type,
        "topic": spec.topic,
        "description": spec.description or "",
        "tags": spec.tags,
        "custom_extensions": spec.custom_extensions,
    }
    if broker_view_id is not None:
        common["target_kafka_view_id"] = broker_view_id

    if isinstance(spec, ElementTriggerSpec):
        common.update({
            "source_view": spec.source_view,
            "source_type": "S3",
            "event_type": spec.event_type,
            "object_key_filters": {
                "prefix": spec.object_key_prefix,
                "suffix": spec.object_key_suffix,
            },
        })
    else:
        if spec.schedule.simple:
            common["schedule"] = {"mode": "simple", "expression": spec.schedule.simple}
        else:
            common["schedule"] = {"mode": "advanced", "expression": spec.schedule.advanced}
    return common


def ensure_trigger(
    cli: VastdeCli,
    spec: TriggerSpec,
    *,
    broker_view_id: int | None = None,
    dry_run: bool = False,
) -> TriggerResult:
    body = _to_body(spec, broker_view_id)
    existing = cli.triggers_get(spec.name)

    if existing is None:
        if dry_run:
            return TriggerResult(spec.name, "would_create")
        cli.triggers_create(spec.name, body)
        return TriggerResult(spec.name, "created")

    # Drift detection — compare only the fields we manage.
    drift = {k: v for k, v in body.items() if existing.get(k) != v}
    if not drift:
        return TriggerResult(spec.name, "unchanged")

    if dry_run:
        return TriggerResult(spec.name, "would_update")
    cli.triggers_update(spec.name, body)
    return TriggerResult(spec.name, "updated")
