"""End-to-end deployment test for the minimal vastde.yaml schema.

Runs the documented steps against a real VMS tenant. Order matters —
later steps fail if earlier ones haven't landed.

  1.  Create group + bucket-owner user           (cluster admin; requires `local_provider_id`)
  2.  Create view policy with flavor=S3_NATIVE   (cluster admin; DATABASE protocol REQUIRES S3_NATIVE)
  3a. Create broker view (S3+DATABASE+KAFKA + kafka_vip_pools)  (cluster admin)
  3b. Create default + dlq topics in the broker bucket          (cluster admin; setup-provisioning
                                                                 does NOT auto-create them)
  4.  POST /api/dataengine/setup-provisioning/    (tenant JWT; vip_pools=[<pool.id>] is required —
                                                  without it the telemetries collector can't bind)
  5.  POST /api/dataengine/mtls-authentication-credentials/  (tenant JWT; returns guid)
  6.  POST /api/dataengine/kubernetes-clusters/   (tenant JWT; creates a cluster-scoped VastTenant
                                                  CR on K8s — see step-6 RECOVERY note below if it
                                                  fails with "Failed to provision telemetries")
  7.  POST /api/dataengine/container-registries/  (tenant JWT; uses the cluster's VRN)

Each step is idempotent: if the resource already exists with the right shape,
it's reused. Safe to re-run.

Catalog corrections this script bakes in (vs. docs/vms-api-full-catalog.md as
originally written) — all live-validated against var203 (VAST 5.4.3 SP4):
  - /groups/ and /users/ POST require `local_provider_id` (was missing from catalog)
  - view-policy flavor enum is NFS|SMB|S3_NATIVE|MIXED_LAST_WINS (was "MIXED")
  - DataEngine broker view with DATABASE protocol REQUIRES flavor=S3_NATIVE
  - Kafka topics must pre-exist in the broker bucket BEFORE setup-provisioning
  - setup-provisioning `vip_pools` field is effectively required (without it, k8s
    cluster registration later fails with the opaque telemetries error)
  - k8s cluster registration creates a cluster-scoped VastTenant CR named after
    the tenant; a stale CR (or one in 5-min `Deleting` state) reproducibly causes
    "Failed to provision telemetries resources" → kubectl cleanup needed (see step 6)

Usage:
    python scripts/test_minimal_enable.py -c sample/nc-tenant.yaml          # dry-run
    python scripts/test_minimal_enable.py -c sample/nc-tenant.yaml --apply  # actually do it
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests
import urllib3
from dotenv import load_dotenv

# Project on path so we can import the loader
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from vastde_orch.config.loader import load_minimal_config
from vastde_orch.config.models_minimal import VastdeMinimalConfig

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ── tiny HTTP helpers ───────────────────────────────────────────────────────

class Vms:
    def __init__(self, addr: str, cluster_user: str, cluster_pass: str,
                 tenant_user: str, tenant_pass: str, tenant_name: str):
        self.base = f"https://{addr}"
        self.cluster_jwt = self._mint("/api/latest/token/", cluster_user, cluster_pass)
        self.tenant_jwt = self._mint(f"/api/latest/token/{tenant_name}/", tenant_user, tenant_pass)
        self.tenant_name = tenant_name

    def _mint(self, path: str, u: str, p: str) -> str:
        r = requests.post(self.base + path, verify=False, timeout=15,
                          json={"username": u, "password": p})
        r.raise_for_status()
        return r.json()["access"]

    def _req(self, method: str, path: str, *, tenant: bool = False, **kw) -> requests.Response:
        h = kw.pop("headers", {})
        h["Authorization"] = f"Bearer {self.tenant_jwt if tenant else self.cluster_jwt}"
        if tenant:
            h.setdefault("X-Tenant-Name", self.tenant_name)
        return requests.request(method, self.base + path, headers=h, verify=False, timeout=60, **kw)

    def cluster_get(self, path: str, **kw) -> Any:
        r = self._req("GET", path, **kw)
        r.raise_for_status()
        j = r.json()
        if isinstance(j, list):
            return j
        return j.get("data") or j.get("results", [])

    def cluster_post(self, path: str, body: dict) -> dict:
        r = self._req("POST", path, json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"POST {path} → {r.status_code}: {r.text[:400]}")
        return r.json()

    def tenant_get(self, path: str) -> Any:
        """Raw JSON response. Caller wraps with as_list() if expecting paginated."""
        r = self._req("GET", path, tenant=True)
        if r.status_code == 410:
            return []
        r.raise_for_status()
        return r.json()

    def tenant_post(self, path: str, body: dict) -> dict:
        r = self._req("POST", path, tenant=True, json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"POST {path} → {r.status_code}: {r.text[:600]}")
        return r.json()


def as_list(j: Any) -> list:
    """Normalize paginated responses (list, {data:...}, or {results:...}) to a list."""
    if isinstance(j, list):
        return j
    if isinstance(j, dict):
        return j.get("data") or j.get("results") or []
    return []


def _read_b64_pem(path: Path) -> str:
    """Read a PEM file and return its base64 encoding (single line)."""
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _step(n: int, label: str) -> None:
    print(f"\n──── step {n}: {label} ────")


def _planned(body: dict, *, max_len: int = 400) -> None:
    import json
    s = json.dumps(body, indent=2)
    if len(s) > max_len:
        s = s[:max_len] + "…(truncated)"
    print(f"  body: {s}")


# ── the 7 steps ─────────────────────────────────────────────────────────────

def run(cfg: VastdeMinimalConfig, apply: bool) -> None:
    addr = cfg.vms.address
    cluster_user = os.environ["VMS_USER"]
    cluster_pass = os.environ["VMS_PASSWORD"]
    tenant_user = os.environ[cfg.vms.auth.user_env]
    tenant_pass = os.environ[cfg.vms.auth.password_env]
    vms = Vms(addr, cluster_user, cluster_pass, tenant_user, tenant_pass, cfg.vms.tenant_name)

    # Resolve tenant_id + vippool_id (cluster admin reads)
    tenant = next((t for t in vms.cluster_get("/api/latest/tenants/") if t["name"] == cfg.vms.tenant_name), None)
    if not tenant:
        sys.exit(f"tenant {cfg.vms.tenant_name!r} not found")
    tid = tenant["id"]
    local_provider_id = tenant.get("local_provider_id") or 1
    vippool = next((p for p in vms.cluster_get("/api/latest/vippools/") if p["name"] == cfg.vip_pool_name), None)
    if not vippool:
        sys.exit(f"vippool {cfg.vip_pool_name!r} not found")
    pid = vippool["id"]
    print(f"\nResolved: tenant_id={tid}  vippool_id={pid}  local_provider_id={local_provider_id}"
          f"  DE_already_enabled={tenant.get('data_engine_enabled')}")

    # ── 1. group ──
    _step(1, f"group {cfg.identity.group.name!r}")
    grps = vms.cluster_get(f"/api/latest/groups/?tenant_id={tid}")
    grp = next((g for g in grps if g["name"] == cfg.identity.group.name), None)
    if grp:
        print(f"  exists: id={grp['id']} (skip)")
    else:
        body = {"name": cfg.identity.group.name, "gid": cfg.identity.group.gid,
                "tenant_id": tid, "local_provider_id": local_provider_id}
        _planned(body)
        if apply:
            grp = vms.cluster_post("/api/latest/groups/", body)
            print(f"  created: id={grp['id']}")

    # ── 1b. bucket-owner user ──
    _step(1, f"user {cfg.identity.bucket_owner.name!r} (bucket owner)")
    users = vms.cluster_get(f"/api/latest/users/?tenant_id={tid}")
    user = next((u for u in users if u["name"] == cfg.identity.bucket_owner.name), None)
    if user:
        print(f"  exists: id={user['id']} (skip)")
    else:
        body = {
            "name": cfg.identity.bucket_owner.name,
            "uid": cfg.identity.bucket_owner.uid,
            "leading_gid": cfg.identity.bucket_owner.leading_gid,
            "allow_create_bucket": cfg.identity.bucket_owner.allow_create_bucket,
            "tenant_id": tid,
            "local_provider_id": local_provider_id,
        }
        _planned(body)
        if apply:
            user = vms.cluster_post("/api/latest/users/", body)
            print(f"  created: id={user['id']}")

    # ── 2. view policy ──
    _step(2, f"view-policy {cfg.view_policy.name!r}")
    pols = vms.cluster_get(f"/api/latest/viewpolicies/?tenant_id={tid}")
    pol = next((p for p in pols if p["name"] == cfg.view_policy.name), None)
    if pol:
        print(f"  exists: id={pol['id']} (skip)")
    else:
        body = {"name": cfg.view_policy.name, "flavor": cfg.view_policy.flavor, "tenant_id": tid}
        _planned(body)
        if apply:
            pol = vms.cluster_post("/api/latest/viewpolicies/", body)
            print(f"  created: id={pol['id']}")

    # ── 3. broker view ──
    _step(3, f"broker view {cfg.broker_view.path!r} (S3+DATABASE+KAFKA)")
    views = vms.cluster_get(f"/api/latest/views/?tenant_id={tid}")
    view = next((v for v in views if v["path"] == cfg.broker_view.path), None)
    if view:
        print(f"  exists: id={view['id']} (skip)")
    else:
        body = {
            "path": cfg.broker_view.path,
            "bucket": cfg.broker_view.bucket,
            "protocols": ["S3", "DATABASE", "KAFKA"],
            "policy_id": pol["id"] if pol else 0,
            "tenant_id": tid,
            "bucket_owner": cfg.identity.bucket_owner.name,
            "bucket_owner_type": "USER",
            "create_dir": True,
            "kafka_vip_pools": [pid],
        }
        _planned(body)
        if apply:
            view = vms.cluster_post("/api/latest/views/", body)
            print(f"  created: id={view['id']}")

    # ── 3b. Kafka topics in the broker bucket ──
    bucket = cfg.broker_view.bucket
    existing_topics_url = f"/api/latest/topics/?tenant_id={tid}&database_name={bucket}"
    existing_topics_raw = vms.cluster_get(existing_topics_url) if apply else []
    existing_topic_names = {t.get("name") for t in (existing_topics_raw or [])}
    for tname in [cfg.setup_provisioning.default_topic_name, cfg.setup_provisioning.dead_letter_topic_name]:
        _step(3, f"topic {tname!r} in broker bucket {bucket!r}")
        if tname in existing_topic_names:
            print(f"  exists (skip)")
            continue
        body = {"database_name": bucket, "name": tname, "topic_partitions": 16}
        _planned(body)
        if apply:
            r = vms._req("POST", f"/api/latest/topics/?tenant_id={tid}&database_name={bucket}", json=body)
            if r.status_code >= 400:
                raise RuntimeError(f"POST /api/latest/topics/ → {r.status_code}: {r.text[:400]}")
            print(f"  created")

    # ── 4. setup-provisioning ──
    _step(4, "DataEngine setup-provisioning")
    current = vms.tenant_get("/api/dataengine/setup-provisioning/")
    if isinstance(current, dict) and current.get("status") == "completed":
        print(f"  already completed (skip)")
    else:
        body = {
            "kafka_broker": {"type": "Internal", "name": cfg.kafka_broker_name},
            "default_topic_name": cfg.setup_provisioning.default_topic_name,
            "dead_letter_topic_name": cfg.setup_provisioning.dead_letter_topic_name,
            "vip_pools": [pid],
        }
        _planned(body)
        if apply:
            vms.tenant_post("/api/dataengine/setup-provisioning/", body)
            for i in range(20):
                time.sleep(3)
                st = vms.tenant_get("/api/dataengine/setup-provisioning/")
                print(f"  poll {i+1}: status={st.get('status')} msg={st.get('message')}")
                if st.get("status") in ("completed", "failed"):
                    if st.get("status") == "failed":
                        sys.exit(f"setup-provisioning failed: {st}")
                    break

    # ── 5. mTLS credentials ──
    _step(5, f"mTLS credential {cfg.k8s.mtls.name!r}")
    creds = as_list(vms.tenant_get("/api/dataengine/mtls-authentication-credentials/"))
    cred = next((c for c in creds if c.get("name") == cfg.k8s.mtls.name), None)
    if cred:
        print(f"  exists: guid={cred.get('guid')} (skip)")
    else:
        body = {
            "name": cfg.k8s.mtls.name,
            "certificate_authority_b64": _read_b64_pem(cfg.k8s.mtls.ca_cert_file),
            "client_certificate_b64":    _read_b64_pem(cfg.k8s.mtls.client_cert_file),
            "client_key_b64":            _read_b64_pem(cfg.k8s.mtls.client_key_file),
        }
        _planned({**body, "client_key_b64": "***", "client_certificate_b64": body["client_certificate_b64"][:40]+"…", "certificate_authority_b64": body["certificate_authority_b64"][:40]+"…"})
        if apply:
            cred = vms.tenant_post("/api/dataengine/mtls-authentication-credentials/", body)
            print(f"  created: guid={cred.get('guid')}")

    # ── 6. k8s cluster ──
    # IMPORTANT: POST /kubernetes-clusters/ synchronously creates a cluster-scoped
    # `VastTenant` CR in the target K8s cluster, named after the tenant (e.g. `nc-tenant`).
    # If a CR with that name already exists OR is in `Deleting` state (the VAST operator
    # has a 300s deletion delay), K8s returns 409 and VAST surfaces the opaque
    # "Failed to provision telemetries resources" error. See
    # docs/research/k8s-registration-investigation-2026-05-31.md for the full chain.
    _step(6, f"k8s cluster {cfg.k8s.name!r}")
    clusters = as_list(vms.tenant_get("/api/dataengine/kubernetes-clusters/"))
    cluster = next((c for c in clusters if c.get("name") == cfg.k8s.name), None)
    if cluster:
        print(f"  exists: vrn={cluster.get('vrn')} (skip)")
    else:
        body = {
            "name": cfg.k8s.name,
            "kube_api_url": cfg.k8s.kube_api_url,
            "mtls_credentials_guid": (cred or {}).get("guid", "<from-step-5>"),
            "namespaces": cfg.k8s.namespaces,
        }
        _planned(body)
        if apply:
            try:
                cluster = vms.tenant_post("/api/dataengine/kubernetes-clusters/", body)
                print(f"  created: vrn={cluster.get('vrn')}")
            except RuntimeError as exc:
                if "Failed to provision telemetries" in str(exc):
                    print(f"\n  ✗ {exc}")
                    print("\n  RECOVERY — stale VastTenant CR likely blocks the create.")
                    print("  Run these on the K8s master (cluster-admin kubectl):")
                    print(f"    kubectl delete vasttenant {cfg.vms.tenant_name} --wait=false")
                    print(f"    kubectl patch vasttenant {cfg.vms.tenant_name} "
                          "--type merge -p '{\"metadata\":{\"finalizers\":null}}'")
                    print(f"    kubectl get vasttenants    # must show: No resources found")
                    print(f"  Then re-run this script (--apply). The other steps are idempotent.")
                raise

    # ── 7. container registry ──
    _step(7, f"container registry {cfg.registry.name!r}")
    regs = as_list(vms.tenant_get("/api/dataengine/container-registries/"))
    reg = next((r for r in regs if r.get("name") == cfg.registry.name), None)
    if reg:
        print(f"  exists: vrn={reg.get('vrn')} (skip)")
    else:
        body = {
            "name": cfg.registry.name,
            "url": cfg.registry.url,
            "primary_kubernetes_cluster": {
                "kubernetes_cluster_vrn": cfg.k8s_cluster_vrn,
                "namespace": cfg.registry.namespace,
            },
            "auth_type": cfg.registry.auth_type,
        }
        if cfg.registry.auth_type == "password":
            body["username"] = os.environ[cfg.registry.username_env or ""]
            body["password"] = os.environ[cfg.registry.password_env or ""]
        _planned({**body, "password": "***" if "password" in body else None})
        if apply:
            reg = vms.tenant_post("/api/dataengine/container-registries/", body)
            print(f"  created: vrn={reg.get('vrn')}")

    print(f"\n{'='*60}")
    print(f"{'APPLIED' if apply else 'DRY-RUN'}: 7 steps complete on {cfg.vms.tenant_name}")
    print(f"{'='*60}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--config", required=True, type=Path)
    ap.add_argument("--apply", action="store_true", help="Actually run the mutations (default: dry-run)")
    args = ap.parse_args()
    load_dotenv(".env")
    cfg = load_minimal_config(args.config, env_file=None)
    run(cfg, apply=args.apply)


if __name__ == "__main__":
    main()
