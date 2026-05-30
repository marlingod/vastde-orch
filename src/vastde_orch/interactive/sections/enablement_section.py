"""Wizard: Stage A enablement section.

Covers: tenant config, K8s availability, container registry, event broker,
identity (group + users), source views.
"""

from __future__ import annotations

from typing import Any

from vastde_orch.interactive._prompts import Prompter
from vastde_orch.interactive._vms_probe import VmsProbe


def _build_kubernetes(p: Prompter) -> dict[str, Any]:
    already = p.confirm(
        "k8s.already_configured",
        "Is Kubernetes already set up for VAST DataEngine (Zarf package deployed)?",
        default=False,
    )
    out: dict[str, Any] = {
        "name": p.text("k8s.name", "Kubernetes cluster name", default="prod-k8s"),
        "api_server": p.text("k8s.api_server", "K8s API server URL"),
    }
    kubeconfig = p.text("k8s.kubeconfig", "Path to kubeconfig (blank for default)", default="")
    if kubeconfig:
        out["kubeconfig"] = kubeconfig

    if not already:
        out["zarf_package_path"] = p.text(
            "k8s.zarf_package_path",
            "Path to zarf-package-dataengine-*.tar.zst",
        )
        out["zarf_init_path"] = p.text(
            "k8s.zarf_init_path",
            "Path to zarf-init-*.tar.zst",
        )
    return out


def _build_registry(p: Prompter) -> dict[str, Any]:
    name = p.text("registry.name", "Container registry name", default="primary-registry")
    base_url = p.text("registry.base_url", "Container registry URL")
    method = p.choice(
        "registry.auth_method",
        "Registry authentication",
        choices=["user_credentials", "kubernetes_secret", "none"],
        default="user_credentials",
    )
    auth: dict[str, Any] = {"method": method}
    if method == "user_credentials":
        auth["username_env"] = p.text(
            "registry.username_env", "Env var for registry username", default="REGISTRY_USER"
        )
        auth["password_env"] = p.text(
            "registry.password_env", "Env var for registry password", default="REGISTRY_PASSWORD"
        )
    elif method == "kubernetes_secret":
        auth["kubernetes_secret_name"] = p.text(
            "registry.k8s_secret_name", "Existing K8s secret name"
        )
    return {"name": name, "base_url": base_url, "auth": auth}


def _build_vast_broker(p: Prompter) -> dict[str, Any]:
    return {
        "kind": "vast",
        "view_path": p.text(
            "broker.view_path", "Broker view path", default="/system/dataengine-broker"
        ),
        "bucket_name": p.text("broker.bucket_name", "S3 bucket name", default="de-broker"),
        "bucket_owner": p.text("broker.bucket_owner", "Bucket owner user name"),
        "vip_pool": {
            "name": p.text("broker.vip_name", "VIP pool name", default="de-broker-vips"),
            "cidr": p.text("broker.vip_cidr", "VIP pool CIDR"),
            "ip_range": [
                p.text("broker.vip_ip_start", "First IP"),
                p.text("broker.vip_ip_end", "Last IP"),
            ],
        },
        "default_topic": {
            "name": p.text("broker.default_topic_name", "Default topic name", default="de-default"),
            "partitions": p.integer(
                "broker.default_topic_partitions", "Default topic partitions",
                default=50, minimum=1,
            ),
            "retention_hours": p.integer(
                "broker.default_topic_retention", "Default topic retention hours",
                default=168, minimum=6,
            ),
        },
        "deadletter_topic": {
            "name": p.text(
                "broker.dlq_topic_name", "Deadletter topic name", default="de-deadletter"
            ),
            "partitions": p.integer(
                "broker.dlq_topic_partitions", "Deadletter topic partitions",
                default=16, minimum=1,
            ),
            "retention_hours": p.integer(
                "broker.dlq_topic_retention", "Deadletter retention hours",
                default=24, minimum=6,
            ),
        },
    }


def _build_kafka_broker(p: Prompter) -> dict[str, Any]:
    return {
        "kind": "kafka",
        "name": p.text("broker.name", "Kafka broker config name", default="kafka-external"),
        "hosts": [p.text("broker.host_0", "Kafka host (FQDN or IP)")],
        "port": p.integer("broker.port", "Kafka port", default=9092),
        "default_topic": {
            "name": p.text("broker.default_topic_name", "Default topic name", default="de-default"),
            "partitions": p.integer("broker.default_topic_partitions", "Partitions", default=50),
            "retention_hours": p.integer(
                "broker.default_topic_retention", "Retention hours", default=168
            ),
        },
        "deadletter_topic": {
            "name": p.text("broker.dlq_topic_name", "Deadletter topic", default="de-deadletter"),
            "partitions": p.integer("broker.dlq_topic_partitions", "Partitions", default=16),
            "retention_hours": p.integer(
                "broker.dlq_topic_retention", "Retention hours", default=24
            ),
        },
    }


def _build_identity(p: Prompter) -> dict[str, Any]:
    group = {
        "name": p.text("identity.group.name", "DataEngine user group name", default="dataengine-users"),
        "gid": p.integer("identity.group.gid", "Group GID", default=5000, minimum=1),
        "provider": p.choice(
            "identity.group.provider",
            "Identity provider",
            choices=["vast", "ldap", "active_directory"],
            default="vast",
        ),
    }

    def build_user(i: int, sub: Prompter) -> dict[str, Any]:
        return {
            "name": sub.text("name", f"User #{i + 1} username"),
            "uid": sub.integer("uid", f"User #{i + 1} UID", minimum=1),
        }

    users = p.loop("identity.users", build_user, add_message="Add another user?")
    policy = p.choice(
        "identity.policy",
        "Identity policy",
        choices=["assign_predefined", "custom"],
        default="assign_predefined",
    )

    out: dict[str, Any] = {"group": group, "users": users, "policy": policy}

    if p.confirm(
        "identity.create_tenant_admin",
        "Create a VMS administrative user (tenant admin)? "
        "Needed to fully enable DataEngine via API.",
        default=True,
    ):
        out["tenant_admin"] = {
            "username": p.text(
                "identity.tenant_admin.username", "Tenant-admin username",
                default="demo-admin",
            ),
            "password_env": p.text(
                "identity.tenant_admin.password_env",
                "Env var holding the tenant-admin password",
                default="DEMO_ADMIN_PASSWORD",
            ),
        }
        role_name = p.text(
            "identity.tenant_admin.role_name",
            "Role name (blank for auto: <tenant>-admin-role)",
            default="",
        )
        if role_name:
            out["tenant_admin"]["role_name"] = role_name

    return out


def _build_source_views(probe: VmsProbe, p: Prompter) -> list[dict[str, Any]]:
    def build_view(i: int, sub: Prompter) -> dict[str, Any]:
        return {
            "path": sub.text("path", f"Source view #{i + 1} path (e.g. /raw/docs)"),
            "bucket": sub.text("bucket", "S3 bucket name"),
            "owner": sub.text("owner", "Bucket owner user name"),
            "policy": sub.text("policy", "View policy name", default="dataengine-default"),
        }

    return p.loop("source_views", build_view, add_message="Add another source view?")


def build_enablement_section(probe: VmsProbe, p: Prompter) -> dict[str, Any]:
    tenant_name = p.text(
        "tenant.name",
        "Tenant name (Stage A target)",
        default="data-platform",
    )
    out: dict[str, Any] = {
        "tenant": {
            "name": tenant_name,
            "create_if_missing": p.confirm(
                "tenant.create_if_missing",
                "Create the tenant if it doesn't exist?",
                default=True,
            ),
        },
        "kubernetes": _build_kubernetes(p),
        "container_registry": _build_registry(p),
    }

    broker_kind = p.choice(
        "broker.kind",
        "Event broker type",
        choices=["vast", "kafka"],
        default="vast",
    )
    out["event_broker"] = (
        _build_vast_broker(p) if broker_kind == "vast" else _build_kafka_broker(p)
    )

    out["identity"] = _build_identity(p)

    src_views = _build_source_views(probe, p)
    if src_views:
        out["source_views"] = src_views

    return out
