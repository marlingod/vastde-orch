"""Wizard: Stage B pipelines section (loop of pipelines, each w/ triggers/functions/flow)."""

from __future__ import annotations

from typing import Any

from vastde_orch.interactive._prompts import Prompter
from vastde_orch.interactive._vms_probe import VmsProbe


def _build_trigger(i: int, sub: Prompter) -> dict[str, Any]:
    kind = sub.choice(
        "type", f"Trigger #{i + 1} type", choices=["element", "schedule"], default="element"
    )
    if kind == "element":
        out: dict[str, Any] = {
            "name": sub.text("name", "Trigger name"),
            "type": "element",
            "source_view": sub.text("source_view", "Source view path (e.g. /raw/docs)"),
            "event_type": sub.choice(
                "event_type",
                "Event type",
                choices=[
                    "ElementCreated",
                    "ElementDeleted",
                    "ElementTagCreated",
                    "ElementTagDeleted",
                ],
                default="ElementCreated",
            ),
            "topic": sub.text("topic", "Target broker topic", default="de-default"),
        }
        suffix = sub.text("object_key_suffix", "Object key suffix filter (blank for none)", default="")
        if suffix:
            out["object_key_suffix"] = suffix
        prefix = sub.text("object_key_prefix", "Object key prefix filter (blank for none)", default="")
        if prefix:
            out["object_key_prefix"] = prefix
        return out
    # schedule
    schedule_mode = sub.choice(
        "schedule.mode", "Schedule mode", choices=["simple", "advanced"], default="simple"
    )
    schedule_expr = sub.text(
        "schedule.expression",
        "Cron expression" if schedule_mode == "simple" else "Quartz expression",
    )
    return {
        "name": sub.text("name", "Trigger name"),
        "type": "schedule",
        "schedule": {schedule_mode: schedule_expr},
        "topic": sub.text("topic", "Target broker topic", default="de-default"),
    }


def _build_function(i: int, sub: Prompter) -> dict[str, Any]:
    return {
        "name": sub.text("name", f"Function #{i + 1} name"),
        "source": sub.text("source", "Source directory (e.g. ./functions/parse-pdf)"),
        "image": sub.text("image", "Container image (registry + path, no tag)"),
    }


def _build_flow(p: Prompter, triggers: list[dict], functions: list[dict]) -> list[dict[str, str]]:
    """Auto-derive a sensible default flow: each trigger → first function, then chain."""
    if p.is_scripted:
        # In scripted mode, take the flow verbatim from answers (or empty).
        try:
            return [{"from": e["from"], "to": e["to"]} for e in p._scripted_value("flow", [])]
        except KeyError:
            return []

    # Interactive: if there's at least one trigger + one function, propose the chain.
    if not triggers or not functions:
        return []
    proposed: list[dict[str, str]] = []
    first_func = functions[0]["name"]
    for t in triggers:
        proposed.append({"from": t["name"], "to": first_func})
    for prev, nxt in zip(functions, functions[1:]):
        proposed.append({"from": prev["name"], "to": nxt["name"]})
    return proposed  # The wizard CLI confirms before writing.


def _build_pipeline(probe: VmsProbe, k8s_cluster: str, i: int, sub: Prompter) -> dict[str, Any]:
    name = sub.text("name", f"Pipeline #{i + 1} name")
    description = sub.text("description", "Description (blank for none)", default="")
    namespace = sub.text("namespace", "K8s namespace", default="vast-dataengine")

    triggers = sub.loop("triggers", _build_trigger, add_message="Add another trigger?")
    functions = sub.loop("functions", _build_function, add_message="Add another function?")
    flow = _build_flow(sub, triggers, functions)

    out: dict[str, Any] = {
        "name": name,
        "namespace": namespace,
        "k8s_cluster": k8s_cluster,
        "triggers": triggers,
        "functions": functions,
        "flow": flow,
    }
    if description:
        out["description"] = description
    return out


def build_pipelines_section(
    probe: VmsProbe, p: Prompter, *, k8s_cluster_name: str
) -> list[dict[str, Any]]:
    """Build a list of pipeline dicts. k8s_cluster_name must match enablement.kubernetes.name."""

    def builder(i: int, sub: Prompter) -> dict[str, Any]:
        return _build_pipeline(probe, k8s_cluster_name, i, sub)

    return p.loop("pipelines", builder, add_message="Add another pipeline?")
