# vastde-orch

Declarative automation for deploying VAST DataEngine on a tenant — plus the
supporting Kubernetes bootstrap, brand kit, and pitch deck used to ship this
to field SEs.

What started as a wizard around the `vastde` CLI has grown into a small
toolchain that codifies the 30+ ordered steps required to enable DataEngine
(many of them undocumented or contradicted by the published API docs). Every
"this took a day to figure out" lesson lives in the code or the catalog now,
not in someone's head.

---

## Repo layout

```
.
├── src/
│   ├── vastde_orch/                   # the CLI + Pydantic schemas + reconciler
│   │   ├── cli.py                     # entry: `vastde-orch validate|enable|apply|status|tenant|...`
│   │   ├── bootstrap/                 # cluster-admin tenant lifecycle
│   │   │   ├── tenant.py              # `tenant create|destroy` — 10-step flow
│   │   │   └── tenant_enable.py       # `tenant enable` — auto-discovery + enable_dataengine
│   │   ├── config/
│   │   │   ├── models.py              # full schema (legacy / wizard / `enable` path)
│   │   │   ├── models_minimal.py      # 9-field tenant-scoped schema (recommended)
│   │   │   └── loader.py              # auto-detects schema via top-level `vip_pool_name`
│   │   ├── clients/                   # vastpy, vastde CLI, kubectl, zarf, docker wrappers
│   │   ├── enablement/                # Stage A: vmstate + k8s bootstrap + bind
│   │   ├── pipelines/                 # Stage B: triggers, functions, pipelines
│   │   ├── interactive/               # wizard + per-resource confirm prompts
│   │   ├── vippool_planner.py         # gap-finding for VIP pool auto-allocation
│   │   └── reconciler.py
│   └── vast_brand/                    # reusable VAST brand kit for python-pptx decks
│       ├── theme.py · widgets.py · deck.py · README.md
├── scripts/
│   ├── setup_tenant.py                # DEPRECATED shim — calls `vastde-orch tenant create|destroy`
│   ├── list_vippools.py               # VIP pool report + auto-pick of free IP ranges
│   ├── test_minimal_enable.py         # end-to-end Stage A runner for the minimal schema
│   └── build_pitch_deck.py            # regenerates docs/vastde-orch-pitch.pptx
├── sample/
│   ├── vastde.template.yaml           # minimal schema template, 9 required fields
│   ├── tenant-setup.example.yaml      # input config for `vastde-orch tenant create`
│   ├── nc-tenant.yaml · gt-tenant.yaml · test-tenant.yaml   # live lab configs
│   ├── demo_tenant.yaml               # full schema example (gitignored variants for live)
│   └── answers_demo_tenant.yaml
├── functions/                         # function source for Stage B (Phase 1 deliverable)
│   └── json-validate/                 # canonical vastde-init scaffold + KB-aligned main.py
├── tests/                             # 288 tests, 100% coverage on models_minimal.py
├── docs/                              # reference docs (see "Documentation" below)
├── documentation/
│   └── steps.md                       # end-to-end deploy runbook + mermaid diagrams
├── packages/                          # zarf .tar.zst files (gitignored)
├── KNOWN_ISSUES.md                    # open gaps + TODO1/2/3 from live deploys
├── DECISIONS.md                       # architecture decisions
├── CLAUDE.md
└── pyproject.toml
```

Standalone but related (different repos at the same parent):
- `../kubernetes/zarf/`     — ansible module for the K8s side of DataEngine
- `../kubernetes/csidriver/` — ansible module for the VAST CSI install

---

## Pick your path

| You want to… | Use |
|---|---|
| **Bootstrap a new tenant** from scratch (tenant + group + bucket-owner + role + manager + VIP pool + view policies + DE identity policy) | `vastde-orch tenant create` |
| **Enable DataEngine** on a tenant you just bootstrapped — minimal YAML, auto-discovers existing state | `vastde-orch tenant enable` |
| **Tear down** the bootstrap (reverse-order, strict inverse) | `vastde-orch tenant destroy` |
| **Tear down DE + pipelines** (with dry-run) | `vastde-orch destroy --plan / --include-enablement` |
| Find a free IP range in a subnet before creating a VIP pool | `python scripts/list_vippools.py` |
| Deploy DataEngine end-to-end on one tenant, **today** | **Minimal schema** + `scripts/test_minimal_enable.py` |
| Deploy DataEngine via the original `vastde-orch enable` CLI (full schema, full Stage A pipeline + interactive prompts) | **Full schema** + `vastde-orch enable` |
| Just bootstrap Kubernetes (zarf + Knative + KEDA + VAST operator) — no VAST side | `../kubernetes/zarf/` ansible module |
| Just install the VAST CSI driver | `../kubernetes/csidriver/` ansible module |
| Build a customer pipeline (triggers, functions) on top of an enabled tenant | `vastde-orch apply` |
| Make a branded VAST presentation | `src/vast_brand/` + `python-pptx` |

---

## Quick start (tenant bootstrap — cluster admin, one-time per tenant)

`vastde-orch tenant create` (cluster-admin) builds everything a tenant needs
on the VAST cluster *before* DataEngine can be enabled — driven by a small
declarative YAML. The 10-step flow mirrors the VAST KB "Configure Prerequisites"
doc + "Provisioning User Access and Permissions for DataEngine" (committed
at `docs/provision-user.pdf`).

The minimum YAML is ~17 lines — every other knob has a sensible default:

```yaml
vms:
  address:  var203.selab.vastdata.com
  user:     ${VMS_USER}                    # cluster-admin (env var)
  password: ${VMS_PASSWORD}

tenant:
  name: my-tenant                          # new tenant to create

identity:
  group:
    name: my-de-users                      # DE app users group
    gid:  75800                            # cluster-unique GID
  bucket_owner:
    name: my-de-owner                      # bucket-owner user (NEW)
    uid:  75800                            # cluster-unique UID

vip_pool:                                  # required for `tenant enable` later
  name:         my-vip-pool
  cidr:         172.200.203.0/24           # subnet to allocate from
  default_size: 3                          # auto-picks the smallest free 3-IP gap
```

Then:

```bash
vastde-orch tenant create  -c my-tenant.yaml --plan     # dry-run
vastde-orch tenant create  -c my-tenant.yaml            # apply
vastde-orch tenant destroy -c my-tenant.yaml --plan     # dry-run teardown
vastde-orch tenant destroy -c my-tenant.yaml --yes      # apply teardown (CI-safe)
```

Optional blocks (only define if you need to override the defaults):
`tenant_admin` (name + password), `view_policies.{nfs,s3}` (names + flavors),
`dataengine_policy` (write-policy name, group binding, opt-in `AllowAllTabular`).
Full annotated example: `sample/tenant-setup.example.yaml`.

The legacy entry point `python scripts/setup_tenant.py -c my-tenant.yaml [--plan|--destroy|--yes]`
still works — it's a thin shim that forwards to the same module — but prints a
deprecation note. Use the `vastde-orch` form going forward.

Steps it runs (every one idempotent):

1. **Tenant**
2. **Group** — auto-scoped to the tenant's local provider (auto-created when the tenant is created; e.g. `provider-<tenant>`)
3. **Bucket-owner user** — same local-provider scoping
4. **Tenant-admin role**
5. **Tenant-admin manager** — name defaults to `<tenant>-admin`, password from `$TENANT_ADMIN_PASSWORD`
6. **VIP pool** — optional; if `ip_range` is omitted, auto-picks the smallest free gap that fits `default_size: 3` IPs in the subnet
7. **View policies** — NFS + S3 (`<tenant>-nfs-policy`, `<tenant>-s3-policy`)
8. **Assign DE group to tenant** — PATCHes `application_users_group_name` on the tenant. REST equivalent of the Web UI's "Assign Group to DataEngine role" checkbox. *Note: alone it does NOT auto-create the policy — see step 9.*
9. **DataEngine identity policy + bind** — matches the KB doc verbatim (Sids `DataengineTablesAccess` + `DataEngineDefault`), bound to the DE group via group-side `s3_policies_ids` PATCH. Named `<tenant>-de-write` because the KB's `data-engine-<tenant>` name is reserved by VMS (POST returns 403). **Required** — verified on usc-tenant that step 8 alone leaves the group without write perms.
10. **(Opt-in) `AllowAllTabular` bind** — broader S3 + Kafka access; off by default

Need to see what IP ranges are free before picking one?

```bash
python scripts/list_vippools.py                           # all pools, grouped by subnet
python scripts/list_vippools.py --size 4                  # suggest smallest gap ≥ 4 IPs
python scripts/list_vippools.py --subnet 172.200.203.0/24 # scope to one subnet
```

> Note: `list_vippools.py` is still a standalone script (not under `vastde-orch`)
> — it's used during config authoring, not as part of a tenant lifecycle.

---

## Quick start (tenant enable — minimal YAML, auto-discovery)

After `tenant create`, almost every field needed by the original full-schema
`enable` flow already exists on VMS. `vastde-orch tenant enable` reads them
back from VMS at run time and constructs the full `EnablementSpec` in memory.
You only declare the things VMS **doesn't** know yet: K8s + container registry.

The YAML is ~13 lines instead of 60:

```yaml
vms:
  address:  var203.selab.vastdata.com
  tenant:   my-tenant
  user:     ${VMS_USER}
  password: ${VMS_PASSWORD}

kubernetes:
  name:             my-tenant-k8s
  api_server:       https://<MASTER_IP>:6443
  kubeconfig:       ~/.kube/my-admin-cert.yaml
  ca_cert_path:     ~/.kube/my-admin-certs/ca.pem
  client_cert_path: ~/.kube/my-admin-certs/client.pem
  client_key_path:  ~/.kube/my-admin-certs/client.key

container_registry:
  name:     my-registry
  base_url: docker.io
  auth: { method: none }
```

Then:

```bash
vastde-orch tenant enable -c my-tenant-enable.yaml --plan   # discovery + dry-run
vastde-orch tenant enable -c my-tenant-enable.yaml          # apply
```

The dry-run prints the discovered state (tenant_id, group, bucket-owner, view
policy, vip_pool, tenant-admin) so you can sanity-check before applying.

Discovery rules (see `src/vastde_orch/bootstrap/tenant_enable.py`):
- **tenant + local_provider_id** — read straight off the tenant record
- **group** — the unique group on the tenant's local provider
- **bucket_owner** — the unique non-system user with `allow_create_bucket=true` (filters out the VAST `dataengine`/`telemetries-collector-*` system users)
- **view_policy** — the unique S3_NATIVE policy on the tenant (matches the dedup logic in `enablement/event_broker.py:_pick_s3_native_policy`)
- **vip_pool** — the unique `PROTOCOLS` pool on the tenant
- **tenant_admin** — the `TENANT_ADMIN` manager on the tenant; password from `$TENANT_ADMIN_PASSWORD`

If any of these is ambiguous (e.g. multiple PROTOCOLS pools), the YAML can
override with `vip_pool_name:` / `group_name:` / `bucket_owner_name:`.

---

## Quick start (minimal schema — the path most SEs should take)

The minimal schema is 9 fields. Everything else is auto-derived.

```bash
pip install -e ".[dev]"
cp .env.example .env                       # fill in VMS_USER, VMS_PASSWORD, REGISTRY_USER, REGISTRY_PASSWORD
cp sample/vastde.template.yaml vastde.yaml # edit the 9 REQUIRED fields

# Validate (works against both schemas; CLI auto-detects)
vastde-orch validate -c vastde.yaml

# Dry-run the 7-step Stage A flow (no mutations)
python scripts/test_minimal_enable.py -c vastde.yaml

# Apply it
python scripts/test_minimal_enable.py -c vastde.yaml --apply
```

The test script runs 7 ordered steps against the live VMS:

1. group + bucket-owner user
2. view-policy (forced `S3_NATIVE` — required for DataEngine broker view)
3. broker view (S3+DATABASE+KAFKA) + Kafka topics
4. `POST /api/dataengine/setup-provisioning/` (with `vip_pools` — the catalog correction)
5. `POST /api/dataengine/mtls-authentication-credentials/`
6. `POST /api/dataengine/kubernetes-clusters/` (auto-detects + prints recovery commands for stuck `VastTenant` CRs)
7. `POST /api/dataengine/container-registries/`

Every step is idempotent. Re-runs are safe.

> The CLI's `enable` command for the **minimal** schema is gated for now
> (prints a clear "not yet wired" message). Use the test script until
> `enable_dataengine_minimal()` lands in the CLI.

---

## Quick start (full schema — wizard + `enable`)

```bash
pip install -e ".[dev]"
cp .env.example .env

# Either author a config interactively…
vastde-orch wizard                     # prompts → writes vastde.yaml

# …or start from a sample
cp sample/demo_tenant.yaml vastde.yaml # edit for your tenant

vastde-orch validate -c vastde.yaml
vastde-orch enable   -c vastde.yaml --plan     # dry-run Stage A
vastde-orch enable   -c vastde.yaml            # apply Stage A
vastde-orch apply    -c vastde.yaml --plan     # dry-run Stage B (pipelines)
vastde-orch apply    -c vastde.yaml            # apply Stage B
vastde-orch status   -c vastde.yaml            # live pipeline status
```

### Interactive flags

| Flag | Effect |
|---|---|
| `--interactive` / `-i` | Prompt before each resource type with `yes / no / details / continue` |
| `--yes-all` / `-y` | Auto-approve everything (CI-safe) |
| `--non-interactive` | Refuse to prompt even on a TTY |
| `VASTDE_NO_INTERACTIVE=1` | Same as `--non-interactive` |
| `wizard --answers-file <path>` | Pre-fill prompts from a YAML — CI-safe |

CI idiom:
```bash
vastde-orch wizard --answers-file answers.yaml -o vastde.yaml
vastde-orch enable -c vastde.yaml --yes-all
vastde-orch apply  -c vastde.yaml --yes-all
```

### Function inner-loop

```bash
vastde-orch function build parse-pdf            # builds + pushes image
vastde-orch apply -c vastde.yaml --only pdf-ingest
```

---

## Prerequisites

**Operator machine** (where you run `vastde-orch` / `ansible-playbook`):

- Python 3.11+
- `vastde` CLI (download from your VAST cluster's docs page)
- `kubectl` + `zarf` (only needed for `enable` Stage A)
- `docker` (only for `function build`)
- `ansible` (only if using the standalone K8s modules)

**VAST cluster** (pre-existing, cluster-admin one-time — or run `vastde-orch tenant create` to create all of this in one shot):

- Tenant exists
- A PROTOCOLS VIP pool bound to the tenant
- A tenant-admin manager + role on that tenant (the `vastde-orch` workflow uses
  the tenant admin; cluster admin is not impersonated)
- A DataEngine identity policy bound to the user group (otherwise application
  users get 403 on `CreateTrigger / CreateFunction / CreatePipeline` — see KB
  doc at `docs/provision-user.pdf`)

**Kubernetes cluster** (pre-existing or via the ansible modules):

- `kubectl` reachable with cluster-admin
- A default StorageClass (or pass `--storage-class` to `zarf init` — see TODO 1 in `KNOWN_ISSUES.md`)
- VAST CSI driver (for prod) — install via `../kubernetes/csidriver/` ansible module

---

## Documentation

Reference docs in `docs/` and `documentation/`:

| File | What it covers |
|---|---|
| [`docs/vms-api-full-catalog.md`](docs/vms-api-full-catalog.md) | **Authoritative reference**: every VMS + DataEngine endpoint we touch, with live-validated body schemas and the 22 corrections vs. the official docs. |
| [`docs/pipeline-runtime-flow.md`](docs/pipeline-runtime-flow.md) | Runtime flow — what happens when a single event fires, end-to-end VAST → K8s → back. |
| [`documentation/steps.md`](documentation/steps.md) | Field runbook for an end-to-end deploy on `dc-tenant` with mermaid diagrams. |
| [`docs/research/k8s-registration-investigation-2026-05-31.md`](docs/research/k8s-registration-investigation-2026-05-31.md) | Diagnostic trail of the "Failed to provision telemetries resources" bug + the `VastTenant` CR / 300s deletion-delay finding. |
| [`docs/vms-endpoints-reference.md`](docs/vms-endpoints-reference.md) | Earlier lab notes — superseded by the full catalog above. |
| [`docs/provision-user.pdf`](docs/provision-user.pdf) | VAST KB: "Provisioning User Access and Permissions for DataEngine" — the source-of-truth for the identity policy that `vastde-orch tenant create` step 9 implements. Lists every `dataengine:*` action. |
| [`docs/stage-b-prd.md`](docs/stage-b-prd.md) | Stage B (pipelines) end-to-end validation PRD: 5-phase plan to ship one working pipeline on usc-tenant. Current state: Phase 1 (`functions/json-validate/`) + Phase 2 (image built + pushed to `docker.selab.vastdata.com`) complete. |
| [`docs/vastde-orch-pitch.pptx`](docs/vastde-orch-pitch.pptx) | 14-slide deck pitching this work — built via `scripts/build_pitch_deck.py`. |
| [`src/vast_brand/README.md`](src/vast_brand/README.md) | How to build branded VAST decks programmatically. |
| [`docs/manual-testing.md`](docs/manual-testing.md) | Hand-run smoke tests by component. |
| [`docs/wi-tenant-reference.md`](docs/wi-tenant-reference.md) | Reference snapshot of the `wi-tenant` working install (what good looks like). |
| [`KNOWN_ISSUES.md`](KNOWN_ISSUES.md) | Open gaps + TODOs 1/2/3 (capability blocks for zarf/storage, namespace semantics, VAST CSI provisioner). |
| [`DECISIONS.md`](DECISIONS.md) | Architecture decisions log. |

---

## Status

**Tests:** 288 passing • 100% coverage on `models_minimal.py` • 78% project-wide
```bash
pytest tests/
```

**Live-validated end-to-end** on `var203.selab.vastdata.com` (VAST 5.4.3 SP4):

| Tenant | Date | Result |
|---|---|---|
| `nc-tenant` | 2026-05-31 | ✓ Full Stage A via `scripts/test_minimal_enable.py` (one-time kubectl cleanup of stale CR needed) |
| `dc-tenant` | 2026-06-01 | ✓ Full Stage A via `vastde-orch enable` (full schema CLI) |
| `gt-tenant` | 2026-05-31 | × Hit one-K8s-cluster-per-tenant constraint at step 6 — documented in `KNOWN_ISSUES.md` |
| Standalone zarf install | 2026-06-03 | ✓ `../kubernetes/zarf/` ansible module on fresh master + worker |
| `ca-tenant` | 2026-06-07 | ✓ Full bootstrap via `vastde-orch tenant create` + Stage A via `vastde-orch enable --skip-k8s-bootstrap` + manual K8s/registry registration via DE Web UI + DE identity policy bound to `ca-de-users`. Drove the new drift-detection fixes (field aliases, type coercion) and the local-provider scoping discovery. |
| `usc-tenant` | 2026-06-10 | ✓ Full bootstrap via `tenant create` + Stage A via `tenant enable` (auto-discovery). Drove the `tenant_enable` module + the system-user filter (`dataengine`/`telemetries-collector-*`) + the `application_users_group_name` myth-busting (PATCH alone doesn't auto-create the policy; step 9 workaround is required). Stage B Phase 1 + 2 deliverable: function image at `docker.selab.vastdata.com/vast-functions/json-validate:dev`. |

---

## Brand kit & pitch deck

`src/vast_brand/` is a reusable python-pptx wrapper that bakes VAST branding
(colors, V watermark, wordmark, two-tone titles) into every slide. The pitch
deck is one user of it.

```python
from vast_brand import VastDeck, Card
deck = VastDeck()
deck.add_title_slide(kicker="Demo", dark1="Branded", cyan_emphasis="in 3 lines.")
deck.save("out.pptx")
```

See `src/vast_brand/README.md` for the full API.

Regenerate the pitch deck:
```bash
python scripts/build_pitch_deck.py        # writes docs/vastde-orch-pitch.pptx
```

---

## Configuration schemas

**Minimal schema** (`sample/vastde.template.yaml`) — 9 required fields, recommended for new deployments:

```yaml
vms:
  address: var203.selab.vastdata.com
  tenant_name: my-tenant
  auth:
    user_env: TENANT_ADMIN_USER
    password_env: TENANT_ADMIN_PASSWORD

vip_pool_name: my-de-vips

k8s:
  kube_api_url: https://10.143.2.250:6443
  mtls:
    ca_cert_file:     ./certs/ca.pem
    client_cert_file: ./certs/client.pem
    client_key_file:  ./certs/client.key

registry:
  url: docker.io
```

Everything else (tenant ID, VRNs, GUIDs, broker name, namespaces, `vip_pools`
on setup-provisioning, etc.) is auto-derived. See
`src/vastde_orch/config/models_minimal.py` for the full schema.

**Full schema** (`sample/demo_tenant.yaml` / `sample/test-tenant.yaml`) — the
original wizard-derived shape; used by `vastde-orch enable` + `apply`. See
`src/vastde_orch/config/models.py`.

The CLI auto-detects which schema a YAML uses (presence of top-level
`vip_pool_name` flags minimal) and dispatches accordingly.

---

## Operator advice

A few patterns that came out of live deploys, baked into the tool:

- **`vip_pools` on setup-provisioning is effectively required** — without it,
  k8s cluster registration later fails with the opaque "Failed to provision
  telemetries resources" error. The minimal schema auto-passes it.
- **DataEngine broker view (S3+DATABASE+KAFKA) requires policy `flavor: S3_NATIVE`**.
  The minimal schema defaults to it.
- **Kafka topics must pre-exist in the broker bucket** before setup-provisioning
  — the script pre-creates them with 16 partitions.
- **`POST /kubernetes-clusters/` creates a cluster-scoped K8s CR named after the tenant**;
  a stale CR (or one in the operator's 300-second deletion delay window) blocks
  re-registration with the same opaque telemetries error. The test script
  detects this and prints the kubectl recovery commands inline.
- **Users/groups are tenant-scoped via the tenant's auto-created local provider**
  (e.g. tenant `ca-tenant` → provider `provider-ca-tenant`), NOT via a
  `tenant_id` field on the user/group. POSTing `tenant_id` on a user/group is
  silently ignored; the user ends up cluster-level (`local_provider.id = 1`)
  and broker-view creation later 404s with `"user with identifier=..., tenant_guid=..., was not found"`.
  `vastde-orch tenant create` reads `local_provider_id` off the tenant record
  and uses it for both group and bucket-owner user.
- **The DataEngine identity policy name `data-engine-<tenant>` is reserved by VMS** —
  POSTing it returns `403 "is reserved. Please, use a different name."` The
  KB doc's intended path is the Web UI "Assign DataEngine identity policy to
  group" checkbox; the REST `enable` flow can't trigger it. `vastde-orch
  tenant create` step 9 creates an equivalent policy (identical document)
  under `<tenant>-de-write` and binds it to the DE group — this is the only
  thing granting write permissions (`CreateTrigger/Function/Pipeline`).
- **Setting `application_users_group_name` on the tenant is NOT sufficient to
  auto-create the DataEngine policy** — verified on usc-tenant 2026-06-10.
  `tenant create` step 8 PATCHes this field (which we believed was the REST
  equivalent of the Web UI "Assign Group to DataEngine role" checkbox), but
  after `tenant enable` completes the `data-engine-<tenant>` policy still
  doesn't appear. The Web UI checkbox must trigger an additional action we
  haven't reverse-engineered. So step 9's `<tenant>-de-write` workaround is
  required, not optional.
- **`s3policies[id].groups` is read-only on PATCH** — silently no-ops. To bind
  a policy to a group, PATCH from the group side via
  `/groups/{id}/.s3_policies_ids`. The policy's `groups` field reflects the
  binding on read but can't be written directly.
- **`POST /users/{id}/access_keys/` REQUIRES `tenant_id` in the body** —
  without it, VMS returns `400 "It is required to provide tenant_id for S3
  Data requests."` `VmsClient.generate_s3_keys()` now requires a `tenant_id=`
  kwarg as a result.
- **VAST creates system users `dataengine` + `telemetries-collector-*` on
  every DE-enabled tenant** — they have `allow_create_bucket=true` and would
  otherwise match auto-discovery of the bucket-owner.
  `tenant_enable.discover_tenant_state()` filters them out by name prefix.

Full list: `docs/vms-api-full-catalog.md` → "Live-validation pass — 2026-05-31".

---

## Dev

```bash
pip install -e ".[dev]"
pytest tests/                              # 288 tests
ruff check src/ tests/
mypy src/                                  # if you opt in
```

Convention: per `CLAUDE.md`, conventional commits (`feat|fix|refactor|docs|test|chore`),
never commit to `main` directly, tests run before every commit.
