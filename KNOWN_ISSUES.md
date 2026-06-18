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

---

## TODO: `pipelines/` module speaks a vastde CLI surface that does not exist

**Discovered on:** 2026-06-11 during the first end-to-end `vastde-orch apply` against wi-tenant on var203. `--plan` had always worked because the dry-run branch shorts out before any CLI call; the real apply is the first time we exercise the `vastde` shell-outs in `pipelines/`.

### What we found

Both vastde versions on hand (Mac `v5.4.1-dev.c0b8b3d5`, .74 `v5.5.0-dev.20440e54`) reject the orchestrator's invocation on three independent grounds:

| Layer | What the orchestrator emits | What the CLI actually wants |
|---|---|---|
| Flag for body | `--file-input -` (stdin) | `-f, --from-file <path>` (file path) |
| Body schema | JSON with `source_view`, `event_type`, `object_key_filters: {prefix,suffix}` | Individual flags (`--source-bucket`, `--name-prefix`, `--name-suffix`, `--tag-prefix`, `--tag-suffix`) plus `--broker-type` + `--broker-name` |
| Event name | `ElementCreated` | `ObjectCreated:*` (similarly `ObjectRemoved:*`, `ObjectTagging:Put`, `ObjectTagging:Delete`) |

So *all* of `triggers_create/triggers_update`, `functions_create/functions_new_revision`, and `pipelines_create/pipelines_update` in `src/vastde_orch/clients/vastde_cli.py` are broken against either vastde version. The dry-run scoreboard hides this — `--plan` returns `would_create` without invoking the CLI.

### Symptom we hit

```
$ vastde-orch apply -c sample/testing/wi-fraud-pipeline.yaml
=== pipeline: wi-fraud-scorer ===
ShellError: expected JSON on stdout but got: Expecting value: line 1 column 1 (char 0)
# real stderr (lost by run_json's wrap): "unknown flag: --file-input"
```

`run_json` in `clients/_shell.py:70` swallows stderr when stdout fails to parse — the actual `unknown flag: --file-input` message never reaches the caller. Worth fixing as part of the rewrite.

### Wi-tenant state after the failed apply

Verified post-failure: triggers list shows only the pre-existing `first-trigger`; functions and pipelines are empty. The CLI rejected the call before any mutation, so wi-tenant is clean.

### Proposed fix

1. **Translate to current vastde flag surface.** Replace `--file-input -` with one of:
   - `-f <tempfile>` writing the body as YAML (matching the CLI's parser), or
   - individual flags assembled per resource type (cleaner — sidesteps schema drift in the YAML format).
2. **Map body fields.** `source_view` (path) → resolve to `source_bucket` (name) via VMS view lookup; `object_key_filters.{prefix,suffix}` → `--name-prefix` / `--name-suffix`; `event_type: ElementCreated` → `--events ObjectCreated:*`.
3. **Resolve `--broker-type` + `--broker-name`** from the enablement event_broker block (Internal + bucket name for VAST broker, External + URL for Kafka).
4. **Same translation for `functions create` and `pipelines create`** — verify the live flag surface before coding (`vastde functions create --help`, `vastde pipelines create --help`).
5. **Stop dropping stderr.** Update `run_json` to include `result.stderr` in the `ShellError` when stdout doesn't parse — the lost `unknown flag` message cost ~30 min of guessing here.
6. **Add a real-CLI integration test** (gated by `VASTDE_BIN` env var). The dry-run unit tests are not enough because they shortcut the CLI invocation.

### Why not fix on master immediately

This work is being done on `experiment/pipeline-build` per the user's explicit Stage A / Stage B separation (master = stable Stage A enablement; experiment branch = pipelines work). Land the fix on the experiment branch, validate end-to-end on wi-tenant, then PR into master.

### Status

- Stage B Phase 3 wi-tenant deploy **paused** as of 2026-06-11.
- Image `docker.selab.vastdata.com/vast-functions/fraud-scorer:11252e4ae821` is already built + pushed (sha256:61fe0db1…) — usable once the orchestrator rewrite lands.
- `sample/testing/wi-fraud-pipeline.yaml` already on `experiment/pipeline-build`; ready to re-run once the orchestrator speaks the right CLI surface.

---

## Live deploy lessons (2026-06-11 .. 2026-06-16)

Eight distinct issues surfaced during end-to-end `tenant create` + `tenant enable` runs on lax-tenant, charlie-tenant, and wi-tenant. Each shipped a focused fix on master; this section consolidates the findings so a future operator hits them at read-time, not deploy-time.

### 1. DataEngine REST endpoints for K8s + container registry live under `/api/dataengine/` — PR #4

**Symptom:** `vastde-orch tenant enable` reported success but no K8s cluster or container registry appeared under the tenant's DataEngine tabs.

**Cause:** `enable.py` called `/api/latest/k8sclusters/` and `/api/latest/containerregistries/`, which 404 on VAST 5.4.3 SP4. A `_try_or_skip_404` wrapper swallowed the 404s and recorded "skipped" outcomes.

**Resolution:** Switched to the canonical DE-API endpoints — `POST /api/dataengine/mtls-authentication-credentials/`, `POST /api/dataengine/kubernetes-clusters/`, `POST /api/dataengine/container-registries/` — all auth'd via the tenant-admin JWT from `POST /api/latest/token/<tenant>/`. New helpers on `VmsClient`: `register_de_mtls_credential`, `register_de_k8s_cluster`, `register_de_container_registry`. `_try_or_skip_404` removed.

### 2. DE-API endpoints require DataEngine to be toggled on first — PR #5

**Symptom:** First run after PR #4 still 500'd on the very first DE-API call.

**Cause:** The DE-API endpoints return 500 on tenants where DataEngine isn't yet enabled. Registration was running before `_toggle_dataengine_on_tenant`.

**Resolution:** Re-ordered `enable_dataengine`. The DE compute resources now register AFTER the toggle. `setup-provisioning`'s real body doesn't need `k8scluster_name` or `container_registry_name`, so toggling first is safe.

### 3. setup-provisioning is asynchronous; reads pass during it, writes don't — PRs #6 → #7

**Symptom:** After PR #5, the next call after the toggle returned `503 {"detail":"Can't access while setup provisioning is not completed"}`.

**Cause:** `POST /api/latest/dataengine/setup-provisioning/` returns 200 quickly but provisions the tenant's DE namespace in the background. Until that finishes, `/api/dataengine/*` accepts GETs but rejects POSTs with 503.

**First attempt (PR #6, superseded):** Probed readiness with `GET /mtls-authentication-credentials/`. The GET returned 200 immediately, the helper declared "ready", the next POST 503'd.

**Resolution (PR #7):** Replaced the GET probe with `_retry_on_setup_provisioning(callable)` — runs the actual write and retries on the specific 503 with 2s → 15s backoff (5 min cap).

### 4. Each `tenant enable` retriggers setup-provisioning; every DE-API write needs the wrapper — PR #11

**Symptom:** Re-runs of `tenant enable` got past mTLS (idempotent-reuse from a previous run) but 503'd on K8s.

**Cause:** PR #7 only wrapped the FIRST DE-API write. When mTLS was idempotent-reuse (no POST), no write confirmed readiness, and the K8s POST fired into an in-progress setup-provisioning cycle.

**Resolution:** Wrap all three DE-API writes (mTLS, K8s, registry) in `_retry_on_setup_provisioning`. Idempotent-reuse paths return instantly; cold-create paths wait as needed.

### 5. `claimed_per_subnet` misses pools declared with a wider CIDR — PR #8

**Symptom:** `vastde-orch tenant create` for charlie-tenant auto-picked `172.200.203.[1-3]` despite a pre-existing `main` pool using `172.200.203.[1-6]`. VMS rejected with `400 "Given range … overlaps with … from vippool main"`.

**Cause:** `claimed_per_subnet` buckets each claim by `(range_start, pool's own subnet_cidr)`. `main` was declared with `subnet_cidr: 16`, so its claim was bucketed under `172.200.0.0/16`. The `/24` lookup returned an empty list.

**Resolution:** New `claims_overlapping_subnet(pools, target)` primitive that ignores each pool's declared cidr, checks raw IP-range overlap with the target subnet, and clips returned ranges to the target's bounds. `bootstrap/tenant.py`'s auto-picker uses it instead of the bucket lookup. 5 unit tests added.

### 6. Pydantic `Path` doesn't expand `~` — PR #10

**Symptom:** YAML cert paths like `~/.kube/k8s-admin-certs/ca.pem` reached `Path.read_bytes()` literal and crashed with `FileNotFoundError: '~/.kube/...'`.

**Cause:** Pydantic stores the YAML string verbatim; `Path.expanduser()` is never called automatically.

**Resolution:** `.expanduser()` added at every consumption site — `vms.py:register_de_mtls_credential` (3 cert paths) and `enablement/k8s_bootstrap.py` (both zarf paths). `clients/kube.py:_kube_env` already expanded the kubeconfig path; this aligns the rest. Unit test writes real cert files under a temp HOME.

### 7. `POST /tenants/` auto-creates a per-tenant local provider that `DELETE /tenants/` doesn't cascade — PR #3

**Symptom:** After `vastde-orch tenant destroy`, re-creating the tenant 400'd with `{"name":["local provider with this name already exists."]}`.

**Cause:** `POST /tenants/` auto-creates a `local_provider` with the tenant's name. `DELETE /tenants/<id>/` removes the tenant but leaves the local provider orphaned.

**Resolution:** `destroy_tenant` captures `local_provider_id` from the tenant record before the tenant delete, then deletes the local provider as step 0'. If the tenant is already gone, sweeps for an orphan by name. Refuses to touch provider id=1 (cluster default).

### 8. VIP pool needs a DNS short name to produce an FQDN — PR #1

**Symptom:** Every `tenant create` produced a VIP pool with an empty `domain_name`; operators had to set it via the VMS UI's DNS Configurations tab post-create.

**Resolution:** New optional `domain_name` field on `VipPoolSpec`. `ensure_vippool` defaults it to the pool name when not set, producing `<pool-name>.<cluster-dns-suffix>`. Pass `""` to opt out, or override.

### Companion behavior that's not a bug but is worth knowing

- **`enable.identity.tenant_admin` is required for the DE-API path.** The orchestrator uses the tenant admin's username + password (from `password_env`) to fetch the per-tenant JWT. Cluster-admin returns 401 on `/api/dataengine/*` endpoints.
- **YAML `kubernetes.name` is the literal cluster name shared across mTLS / K8s registration / FQDN.** Reusing the same name across tenants (e.g. `kubernetes.name: amer-tenant-k8s` in charlie-tenant's YAML) is allowed — one K8s cluster can serve multiple tenants. Operator typos like `name: -tenant-k8s` (leading dash) are caught with a clear refusal message before any DE-API write.
