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
