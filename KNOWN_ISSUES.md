# Known Issues

## VMS API surface for DataEngine differs from PDF docs

**Discovered on:** 2026-05-28 against VAST 5.4.3 SP4 (build `release-5.4.3-sp4-2420502`) at `var203.selab.vastdata.com`.

### What we found by probing live

We pulled the full swagger spec (`/api/latest/swagger.json` — 620 paths) and grepped for DataEngine-related endpoints. Result:

| Resource | What the PDF implies | What VMS REST actually exposes |
|---|---|---|
| Tenants, views, viewpolicies, users, S3 keys | `/tenants/`, `/views/`, `/viewpolicies/`, `/users/` | ✅ exists, code is correct |
| Event broker (VAST Kafka) | `/eventbrokers/` | ✅ exists (`GET`, `POST`, `PATCH`, `DELETE` on `/eventbrokers/{id}/`) |
| External Kafka broker | `/kafkabrokers/` | ✅ exists |
| Topics | `/topics/` | ✅ exists, but list requires `database_name` query param |
| Triggers | `/triggers/` (assumed) | ✅ exists at `/data/engine/triggers/` (note the slash inside `/data/engine`) |
| Functions | `/functions/` (assumed) | ❌ **not in public swagger** |
| Pipelines | `/pipelines/` (assumed) | ❌ **not in public swagger** |
| K8s cluster registration | `/k8sclusters/` | ❌ **not in public swagger** |
| Container registry registration | `/containerregistries/` | ❌ **not in public swagger** |
| Tenant DataEngine enable/disable | `/tenants/{id}/` PATCH `data_engine_enabled` | ✅ field is on the tenant resource itself |

The DataEngine UI is served as an Angular SPA at `/dataengine/`. Its backend endpoints for functions / pipelines / k8s clusters / container registries are not part of the public VMS swagger — they are either embedded in the SPA's own backend or only accessible via the `vastde` CLI.

### What this means for our code

- `src/vastde_orch/clients/vms.py:ensure_k8scluster()` → will 404 on this VAST version.
- `src/vastde_orch/clients/vms.py:ensure_container_registry()` → will 404 on this VAST version.
- `src/vastde_orch/enablement/enable.py` calls both of the above and will fail at those steps.

The rest of `enable.py` (tenant ensure, view policy, vippool, view, event broker, topics, identity, source views) uses endpoints that **do** exist in the swagger — so those steps work.

For triggers / functions / pipelines we already shell out to the `vastde` CLI (see `src/vastde_orch/clients/vastde_cli.py`), so those work regardless of REST surface.

### Workaround for testing on this cluster

Use `enable --skip-k8s-bootstrap` and avoid the k8s/registry steps:

```bash
vastde-orch enable -c vastde.yaml --skip-k8s-bootstrap --plan
```

You will still see the `ensure_k8scluster` / `ensure_container_registry` calls fail in the plan output — they need to be guarded.

### Proposed fix (not implemented yet)

1. **Short-term**: in `enable.py`, wrap `ensure_k8scluster` and `ensure_container_registry` with a try/except that logs "endpoint not present on this VMS version — register via DataEngine Web UI / vastde CLI" and continues. This keeps the rest of the orchestrator working.

2. **Medium-term**: shell out to `vastde k8sclusters add` / `vastde containerregistries add` (verify exact subcommands by running `vastde --help` against this cluster). Mirror the existing `vastde_cli.py` shell-out pattern.

3. **Long-term**: if VAST publishes a separate DataEngine REST API on a future version (e.g. `/dataengine/api/v1/`), switch to that and keep the `vastde` CLI fallback for older clusters.

### Verification

To re-confirm the gap on a different cluster:

```bash
set -a; source .env; set +a
/usr/bin/curl -sk -u "${VMS_USER}:${VMS_PASSWORD}" \
  "https://${VMS_ADDRESS}/api/latest/swagger.json" -o /tmp/swagger.json
python3 -c "
import json, re
spec = json.load(open('/tmp/swagger.json'))
pat = re.compile(r'k8s|container|registry', re.I)
for p in sorted(spec.get('paths', {})):
    if pat.search(p): print(p)
"
```

If that prints nothing on the target cluster, the gap exists there too.

---

## TODO: schema gaps surfaced by the dc-tenant live deploy

**Discovered on:** 2026-06-01 enabling DataEngine on `dc-tenant` (var203). Both gaps cause the orchestrator to fail or behave inconsistently in ways that aren't visible to the YAML author until the run is already mid-flight.

### TODO 1 — Capability-based `kubernetes` block (zarf + storage prereqs)

**What's missing:** The current `KubernetesSpec` is mostly raw connection data plus package file paths. The two cluster-side prerequisites that actually need to exist before `vastde compute-clusters link` can succeed — Zarf and a default StorageClass — are implicit, undetected, and unmanaged. When either is absent the orchestrator hangs for ~15 minutes and dies with `context deadline exceeded`.

**Why it matters:**
- Vanilla kubeadm clusters ship with **no default StorageClass**. zarf's docker-registry PVC sits `Pending`, registry pods never schedule, `zarf init` times out on `zarf-seed-registry`. The VAST KB (`Enabling DataEngine on a VAST Cluster Tenant`) calls this out explicitly:
  > The above call assumes that a default storage class exists. Otherwise, add the `--storage-class` option to the call. For example `--storage-class=local-path`.
- Re-runs need to detect "zarf is already installed" (currently handled by `kubectl_namespace_exists("zarf", ...)` in `k8s_bootstrap.py`) but the same idea should apply to storage so we don't double-install local-path-provisioner.

**Proposed model — capability blocks with `detect: true` and a typed installer choice:**

```yaml
kubernetes:
  name: dc-k8s-cluster
  kube_api_url: https://10.143.2.247:6443
  mtls: { ca_cert_file: …, client_cert_file: …, client_key_file: … }
  namespaces: [vast-dataengine]

  # Bootstrap zarf if not already present in the cluster
  zarf:
    detect: true                  # check `zarf` namespace; skip install if present
    packages:
      source: local               # local | download
      # source: local — read from this repo's packages/ dir (default)
      init_path:       ./packages/zarf-init-amd64-v0.60.0.tar.zst
      dataengine_path: ./packages/zarf-package-dataengine-amd64-1.0.0.tar.zst
      # source: download — fetch from a URL given by VAST SE
      # version: v0.60.0
      # release_url: https://github.com/zarf-dev/zarf/releases/download/{version}/zarf-init-amd64-{version}.tar.zst

  # Ensure a usable default StorageClass exists
  storage:
    detect: true                  # if a default StorageClass exists, do nothing
    provisioner: local-path       # local-path | vast-csi | none
    # provisioner: vast-csi → see TODO 3 (install script provided by VAST SE)
```

**Semantics of `detect`:**
- `detect: true` (default): run "does it exist?" check; install only if absent. Idempotent.
- `detect: false`: skip the check entirely. Pair with the action implied by `source`/`provisioner`. Useful for "I've installed this out-of-band, don't touch".

**Semantics of `source: download`:** the URL is trusted (provided by VAST SE). **No checksum validation.** Failure mode is a normal HTTP/TLS error. If `release_url` is unset, fall back to `source: local`.

**Code changes required:**
1. `src/vastde_orch/config/models.py:KubernetesSpec` — add nested `ZarfSpec` and `StorageSpec` Pydantic models (replacing the bare `zarf_init_path` / `zarf_package_path` fields, which become `zarf.packages.init_path` / `zarf.packages.dataengine_path`). Keep old fields as deprecated for one release.
2. `src/vastde_orch/clients/kube.py` — add `kubectl_default_storageclass_exists()` and `install_local_path_provisioner()`; `zarf_init` already accepts `storage_class`, so just thread it through.
3. `src/vastde_orch/enablement/k8s_bootstrap.py` — replace the current monolithic flow with a `detect → maybe install` block for each capability (zarf, storage).
4. `clients/_shell.py` — if `source: download`, pull the URL with `curl -L --fail` into a temp dir; no checksum.
5. Update `config/vastde.example.yaml` and `sample/vastde.template.yaml` with the new schema; provide a backward-compat note in `DECISIONS.md`.

**Preflight bonus:** preflight should refuse to start if zarf is unavailable and `zarf.detect: false`, or if storage is unavailable and `storage.detect: false`. Fail-fast beats 15-min zarf timeouts.

### TODO 2 — `kubernetes.namespaces` default is inconsistent with the bootstrap code

**What's inconsistent:** `KubernetesSpec.namespaces` defaults to `["vast-dataengine"]`, but `clients/kube.py:_VAST_NAMESPACES` hardcodes three namespaces that the bootstrap actually creates and labels:
```python
_VAST_NAMESPACES = ["vast-dataengine", "knative-eventing", "knative-serving"]
```
The YAML field is effectively cosmetic on the bootstrap side — bootstrap ignores it and creates all three regardless. But the same field is what gets sent to `vastde compute-clusters link --namespaces=...`, where it *does* matter (it tells VMS which namespaces DataEngine is allowed to deploy into).

**Why it matters:**
- A user who shrinks `namespaces` to `[vast-dataengine]` thinking they're scoping the bootstrap will be surprised when `knative-eventing` and `knative-serving` show up anyway.
- The same field has two different semantics depending on caller (bootstrap = "label these" vs. link = "DataEngine deploy targets").
- On this run we hit a related ambiguity at `vastde compute-clusters link` — the call succeeded with all three namespaces passed, but it's not documented whether all three are required for VMS or if just `vast-dataengine` is enough.

**Proposed fix:**
1. Either:
   - **Make `_VAST_NAMESPACES` derive from `spec.namespaces`** (rename the field or add `ensure_vast_namespaces(namespaces=spec.namespaces)` and let the user opt in/out), OR
   - **Keep bootstrap hardcoded but rename the YAML field** to `deploy_namespaces` (with the default still `[vast-dataengine]`) to make clear it controls only the DataEngine deploy-target list, not the bootstrap namespaces.
2. Document the relationship between this field and the auto-labeled namespaces in `config/vastde.example.yaml`.
3. Verify against VMS: does `vastde compute-clusters link` need all three namespaces, or does the operator handle the knative ones implicitly?

---

### TODO 3 — VAST CSI as a `storage.provisioner` option

**What this is:** when `kubernetes.storage.provisioner: vast-csi`, install the VAST CSI driver onto the target cluster so DataEngine workloads can use VAST-backed persistent volumes (proper persistence; local-path is fine only for the dev / lab path).

**Short-term shape (script-based):**

```yaml
kubernetes:
  storage:
    detect: true
    provisioner: vast-csi
    vast_csi:
      install_script: ./scripts/install-vast-csi.sh
      # script is provided by the VAST SE; trusted, no checksum
      # script env can read VAST_*, KUBECONFIG, etc.
```

The orchestrator shells out to the script via `clients/_shell.py` (matching the project rule that all shell-outs go through `clients/`). The script is responsible for `kubectl apply`-ing the VAST CSI manifests, creating the StorageClass, and marking it default.

**Long-term shape (zarf package):**

Replace the script with a proper zarf package (`vast-csi-driver-amd64-v*.tar.zst`) deployed via `zarf package deploy`, matching how the DataEngine package itself is delivered. This keeps the operator-machine surface area to a single tool (zarf) and gives air-gapped installs the same offline guarantees.

**Open questions for the VAST SE:**
- Does the CSI installer need NFS client tools on the host OS? (CLAUDE.md says yes — make sure ansible playbook 02 installs `nfs-common` on all k8s nodes; already done.)
- Which VIP pool does the CSI mount against? Is it the same as the broker's pool or a separate one?
- Does the installer create the StorageClass with `is-default-class: true` or do we patch it afterwards?

---

## TODO: other gaps surfaced by the same deploy (lower-priority)

These are not schema gaps but operator-experience gaps; capturing them so they don't get lost.

- **Bundled `packages/zarf` is Linux x86_64 only.** The README lists `zarf` as an operator-machine prerequisite but the project ships a Linux ELF binary. On a macOS operator the documented workflow is "scp packages to the master node and run zarf there", but neither the README nor the YAML hint at this. Add to README troubleshooting and consider shipping macOS/arm64 zarf binaries (or a `Makefile` target that downloads the right one).

- **`vastde compute-clusters link` requires cluster-admin VMS creds, not tenant-admin.** Despite `identity.tenant_admin` being designed for tenant-scoped operations, the cluster link step calls a VMS endpoint that returns `400 Failed to provision telemetries resources` with tenant-admin creds. Cluster-admin + `--tenant <name>` works. This isn't currently captured anywhere — neither in `docs/vms-endpoints-reference.md` nor in code comments. Worth documenting and possibly auto-detecting in a future shell-out path.

- **HPA min=5 for `vast-telemetries-collector` blocks small-cluster deploys.** A 2-node lab cluster (1 master, 1 worker, 4 CPU each) cannot fit 5 collectors at 500m CPU without untainting the master. The Helm chart deploys and pods schedule fine, but `zarf package deploy` Helm-fails with `context deadline exceeded` because the HPA can never reach min replicas. Either: (a) make HPA min configurable via a zarf `--set` value, or (b) document the minimum cluster size, or (c) preflight-check schedulable CPU.
