# K8s Cluster Registration Failure — Root Cause Investigation

**Date:** 2026-05-31  
**Cluster:** var203.selab.vastdata.com (VAST 5.4.3 SP4)  
**Tenant:** nc-tenant (id=16, guid=e3304537-48a5-4f2d-aa02-875af1f93d72)  
**K8s cluster:** 10.143.2.242:6443  
**Error:** `HTTP 400 — {"detail":"Failed to provision telemetries resources in https://10.143.2.242:6443"}`

---

## TL;DR — Root Cause and Fix

**Root cause:** `POST /api/dataengine/kubernetes-clusters/` synchronously attempts to CREATE a cluster-scoped `VastTenant` CR (Custom Resource) named `nc-tenant` in the target K8s cluster. If a `VastTenant` CR with that name already exists — either from a prior successful registration or stuck in a `Deleting` state due to the operator's 300-second deletion delay — K8s returns a 409 Conflict, which VAST surfaces as the 400 "Failed to provision telemetries" error.

**Immediate fix (two steps):**

```bash
# Step 1: Remove the stale VastTenant CR (force-bypass the 300s deletion delay)
export KUBECONFIG=~/.kube/vastde-admin.yaml  # or whatever gives cluster-admin access to 10.143.2.242
kubectl get vasttenants  # confirm "nc-tenant" exists
kubectl delete vasttenant nc-tenant
kubectl patch vasttenant nc-tenant --type merge -p '{"metadata":{"finalizers":null}}'
kubectl get vasttenants  # confirm empty

# Step 2: Re-POST (use a CURRENT valid mtls_credentials_guid — verify it still exists first)
curl -sk -H "Authorization: Bearer $ACCESS" -H "X-Tenant-Name: nc-tenant" \
  -H "Content-Type: application/json" \
  "https://var203.selab.vastdata.com/api/dataengine/kubernetes-clusters/" \
  -d '{"name":"nc-k8s-cluster","kube_api_url":"https://10.143.2.242:6443","mtls_credentials_guid":"<guid>","namespaces":["vast-dataengine"]}'
```

**The correct namespace is `vast-dataengine`** (not `vast-data-engine` and not `nc-tenant-vast`). Both exist on the cluster but only `vast-dataengine` has the `vast-serverless` Helm release that VAST expects.

---

## Evidence

### 1. What VAST does internally on POST /kubernetes-clusters/

Confirmed via live experimentation against the running K8s cluster (kubeconfig at `~/.kube/vastde-admin.yaml`):

1. **Synchronously** validates body fields.
2. **Synchronously** calls the K8s API (using the mTLS creds) to `CREATE` a cluster-scoped `VastTenant` CR:
   ```yaml
   apiVersion: vast.vastdata.com/v1
   kind: VastTenant
   metadata:
     name: <tenant_name>          # e.g. "nc-tenant" — NOT the guid
     labels:
       vast_tenant: <tenant_guid>
   spec:
     tenantGUID: <tenant_guid>    # e.g. "e3304537-48a5-4f2d-aa02-875af1f93d72"
     targetNamespaces:
       - <namespace>              # matches the "namespaces" field in the POST body
   ```
3. If CREATE **succeeds** → returns **HTTP 201** with the cluster record (status=null).
4. If CREATE **fails** (K8s returns 409 because a CR with the same name already exists) → returns **HTTP 400** with `"Failed to provision telemetries resources in <url>"`.
5. **Asynchronously** (after the 201), the `vast-operator-controller-manager` picks up the `VastTenant` CR, adds the `vasttenant-controller` finalizer, and patches the `vast-telemetries-collector-config` Secret in the `vast-dataengine` namespace to inject per-tenant S3 credentials:
   ```yaml
   # /data/v1alpha1.yaml decoded:
   apiVersion: collector.vastdata.com/v1alpha1
   kind: CollectorConfig
   tenantConfigs:
     e3304537-48a5-4f2d-aa02-875af1f93d72:
       s3AccessKey: <key>
       s3SecretKey: <secret>
       tenantName: nc-tenant
       vdbEndpoints:
         - nc-vipool.selab-var203.selab.vastdata.com:80
   ```

**Sources:** Live operator logs (`kubectl logs -n vast-dataengine deployment/vast-operator-controller-manager`), direct K8s API observation. The operator code path is `vasttenant/controller.go`.

### 2. The VastTenant CRD schema

```
kubectl get crd vasttenants.vast.vastdata.com -o yaml
```

Key facts:
- **Scope: Cluster** (not namespace-scoped). The name is the tenant's short name (e.g. `nc-tenant`), not its GUID.
- Required spec field: `tenantGUID` (string).
- Optional spec field: `targetNamespaces` (list of strings).
- Optional spec field: `deletionDelay` (string duration).
- Finalizer added by operator: `vasttenant-controller`.
- **Default deletion delay: 300 seconds** (5 minutes), confirmed from operator log: `"deletionDelay":300`. This is the operator's hardcoded default when `spec.deletionDelay` is not set — contradicting the CRD description which says "defaults to immediate deletion (0 seconds)".

Source: `kubectl get crd vasttenants.vast.vastdata.com -o json`, operator logs at `2026-05-31T05:14:57Z`.

### 3. The vast-telemetries-collector-config Secret

Pre-exists in `vast-dataengine` namespace, created by the `vast-serverless` Helm release on 2026-05-29:
```
Name:         vast-telemetries-collector-config
Namespace:    vast-dataengine
Annotations:  meta.helm.sh/release-name: vast-serverless
Labels:       helm.sh/chart: vast-telemetries-collector-0.1.0
```

The Helm install pre-populated this secret with nc-tenant GUID credentials (S3 keys and vdbEndpoints). These creds are **overwritten** by the VAST operator each time a VastTenant CR is reconciled. The secret is NOT the cause of the 400 — the cause is the VastTenant CR conflict. The secret content is managed by the operator, not checked synchronously by VMS.

Source: `kubectl get secret vast-telemetries-collector-config -n vast-dataengine -o json`, decoded at 2026-05-31T05:02 and 05:14.

### 4. Root cause of the "consistent" failure

The prior Helm install of `vast-serverless` in `vast-dataengine` pre-configured the cluster for nc-tenant. This means a prior VMS registration attempt (before this session) **succeeded** (HTTP 201), creating a `VastTenant` CR named `nc-tenant`. When the user deleted the K8s cluster from VAST's side (via the UI or API), VAST set a `deletionTimestamp` on the VastTenant CR. The operator started the 300-second deletion countdown. If the user retried the POST within those 5 minutes, the VastTenant CR was still present (in `Deleting` state) → K8s returned 409 on the CREATE attempt → VAST returned 400.

This also explains why the user tried both `vast-dataengine` and `nc-tenant-vast` namespaces and both failed: the VastTenant CR is cluster-scoped and named after the tenant (`nc-tenant`), not after a namespace. Changing the namespace argument doesn't bypass the name conflict.

Additionally confirmed: when VAST's DELETE endpoint removes the k8s cluster record, it also **deletes the associated mTLS credential** from its own DB. Attempting to re-use the old `mtls_credentials_guid` after a cluster DELETE will fail with 404. A fresh mTLS credential POST is required before each registration attempt.

Source: Live curl tests at 05:02, 05:14, 05:16, 05:21 (2026-05-31). Operator logs tracing the full CREATE→DELETE→requeue cycle.

### 5. Namespace selection

The K8s cluster has two VAST-related namespaces:
- `vast-dataengine` — contains the `vast-serverless` Helm release with all operator components. **This is the correct namespace.**
- `vast-data-engine` — exists but is NOT the Helm release namespace. Registering with this namespace may fail for a different reason (missing operator context) or may fail because of an existing VastTenant CR in a prior state.

The `GET /api/dataengine/mtls-authentication-credentials/<guid>/namespaces?kube_api_url=...` endpoint lists all accessible namespaces and confirms both exist.

### 6. DELETE behavior and the 5-minute cliff

When `DELETE /api/dataengine/kubernetes-clusters/<guid>` is called:
1. VAST removes the k8s cluster record from its DB.
2. VAST removes the associated mTLS credential from its DB.
3. VAST sets `deletionTimestamp` on the VastTenant CR in K8s.
4. The operator starts a **300-second** countdown before removing the finalizer and fully deleting the CR.
5. Any re-registration attempt during that 300 seconds returns 400.
6. After 300 seconds, the operator removes the finalizer and the CR is gone.

The 5-minute window is the "deletion delay" which, per operator code comments, exists to allow in-flight pipelines to drain before the tenant is deregistered. There is no documented way to override this via the API. The only bypass is the `kubectl patch` finalizer removal shown in the fix above.

---

## Fix Candidates (ranked by confidence)

### Candidate 1 — Force-remove VastTenant CR + fresh registration (PROVEN, confidence 100%)

Confirmed working in live testing. Done at 2026-05-31T05:21:41Z.

```bash
export KUBECONFIG=~/.kube/vastde-admin.yaml  # or use vastde-admin.yaml

# 1a. Check current state
kubectl get vasttenants
# Expected: NAME=nc-tenant (if CR exists) or No resources found

# 1b. If CR exists (even in Deleting state), force-remove finalizer and delete
kubectl delete vasttenant nc-tenant 2>/dev/null || true
kubectl patch vasttenant nc-tenant --type merge -p '{"metadata":{"finalizers":null}}' 2>/dev/null || true
# Wait 3 seconds
sleep 3
kubectl get vasttenants  # must show: No resources found

# 1c. Ensure you have a valid (non-deleted) mTLS credential GUID
# Check: GET /api/dataengine/mtls-authentication-credentials/
# If empty or if you need to re-create:
# POST /api/dataengine/mtls-authentication-credentials/ with name, client_key_b64, client_certificate_b64, certificate_authority_b64

# 1d. POST the k8s cluster
curl -sk -H "Authorization: Bearer $ACCESS" -H "X-Tenant-Name: nc-tenant" \
  -H "Content-Type: application/json" \
  "https://var203.selab.vastdata.com/api/dataengine/kubernetes-clusters/" \
  -d '{
    "name": "nc-k8s-cluster",
    "kube_api_url": "https://10.143.2.242:6443",
    "mtls_credentials_guid": "<current-valid-guid>",
    "namespaces": ["vast-dataengine"]
  }'
# Expected: HTTP 201 with the cluster record
```

### Candidate 2 — Wait 5 minutes after DELETE, then retry (simple but slow, confidence 100%)

If you deleted the cluster via VAST UI or API, wait 300+ seconds for the operator to fully remove the VastTenant CR. Then:
- Create a new mTLS credential (the old one was deleted by VAST's DELETE).
- POST the k8s cluster with the new credential GUID.

### Candidate 3 — Use PUT /kubernetes-clusters/<guid> instead of POST (untested, confidence 40%)

If VAST exposes a PUT endpoint that does an upsert (update or create), it might bypass the 409 conflict. The API catalog shows `PUT /kubernetes-clusters/{guid}` with the same body schema. However, this requires knowing the GUID of an existing record, which is only available if the record wasn't fully deleted from VAST's DB. Not applicable to the "consistent failure" scenario where the VAST DB record is already gone.

### Candidate 4 — Delete-data-engine + re-enable + re-register (nuclear option, confidence 90%)

If none of the above works:
```bash
# Disable DataEngine (destroys all DE resources on the tenant)
curl -sk -H "Authorization: Bearer $ACCESS" -H "X-Tenant-Name: nc-tenant" \
  -X DELETE "https://var203.selab.vastdata.com/api/dataengine/remove-data-engine/" \
  -H "Content-Type: application/json" -d '{"force": true}'

# Then: kubectl delete vasttenant nc-tenant + force finalizer removal (as above)

# Then: re-enable DataEngine (POST /setup-provisioning/)
# Then: re-register k8s cluster
```

---

## What Gets Provisioned — "Telemetries Resources" Defined

When `POST /kubernetes-clusters/` succeeds, VAST provisions the following:

1. **K8s: VastTenant CR** (cluster-scoped, created synchronously by VMS):
   ```yaml
   name: <tenant_name>
   spec.tenantGUID: <tenant_guid>
   spec.targetNamespaces: [<namespace>]
   ```

2. **K8s: vast-telemetries-collector-config Secret update** (async, by operator within ~5 seconds):
   Operator patches this Secret in the registered namespace to add per-tenant S3 credentials and VDB endpoints. The telemetry collector pods read this secret to know where to ship logs/traces.

3. **VAST DB: KubernetesCluster record** with status=null (this is normal; "Ready" status may never be set, as confirmed for wi-tenant's kb-master cluster registered 5 days ago — also still status=null).

The terms "provisioning telemetries resources" and "provision telemetries" in error messages refer specifically to step 1+2 above — establishing the telemetry pipeline between the VAST cluster and the K8s-hosted telemetry collector.

---

## Prerequisites Checklist for K8s Cluster Registration

From the docs (vast.pdf pp. 7-11) and live observation:

| Prerequisite | Status for 10.143.2.242 | Verified |
|---|---|---|
| Zarf DataEngine package installed on K8s cluster | Yes — vast-operator, KEDA, Knative, vast-telemetries-collector all Running | Confirmed via kubectl |
| `vasttenant.vast.vastdata.com` CRD exists | Yes | `kubectl get crd` |
| `vastpipelines.vast.vastdata.com` CRD exists | Yes | `kubectl get crd` |
| `vastkafkabrokers.vast.vastdata.com` CRD exists | Yes | `kubectl get crd` |
| VastTenant CR `nc-tenant` does NOT exist | **MUST be clean** | Key failure point |
| mTLS cert CN=vast-dataengine, O=vast-system | Yes | cert decoded |
| K8s group `vast-system` bound to `cluster-admin` | Yes (user confirmed) | |
| K8s API reachable from VMS (var203) | Yes | tcpdump confirmed |
| `vast-dataengine` namespace exists with correct label | Yes | kubectl confirmed |
| DataEngine enabled on nc-tenant (`setup-provisioning` completed) | Yes — completed at 04:46 | `GET /setup-provisioning/` |
| Valid mTLS credential GUID in VAST DB (not previously deleted) | **Must be fresh** | Key failure point |

---

## What Is Still Unknown

1. **Why the original Helm install pre-populated the `vast-telemetries-collector-config` secret** with nc-tenant credentials before any VAST registration. This suggests someone passed nc-tenant GUID as a Helm values parameter during the initial `zarf package deploy`. This is probably benign (the operator overwrites it), but the Helm-owned secret with conflicting data could theoretically cause issues if the operator's PATCH is blocked by a Helm admission webhook in future VAST versions.

2. **The source of the `deletionDelay=300` default**. The CRD spec says "defaults to immediate deletion" but the operator hardcodes 300 seconds. This may be configurable in the operator's ConfigMap or environment but no documentation was found.

3. **Whether `PUT /kubernetes-clusters/<guid>` is idempotent** (would bypass the 409 conflict without requiring VastTenant cleanup). The API catalog confirms PUT exists; untested.

4. **Why the `kafka-auth` Secret appeared in `vast-dataengine` namespace** (created ~10 minutes before our session started, not from Helm). Possibly from a prior registration attempt by the user.

5. **Why VAST deletes the mTLS credential when the k8s cluster record is deleted**. This is undocumented behavior. It means re-registration always requires both a fresh mTLS credential AND a clean VastTenant CR — a two-step requirement that isn't obvious from the API docs.

---

## Source Table

| Source | Location | Key Finding |
|---|---|---|
| VAST PDF p.7-11 | `docs/vast.pdf` pages 7-11 | Zarf package installs VAST Operator, Telemetry Collector, Knative |
| VAST PDF p.34-35 | `docs/vast.pdf` pages 34-35 | K8s cluster registration UI walkthrough; no mention of deletion delay |
| vms-api-full-catalog.md Section A.3 | `docs/vms-api-full-catalog.md` | POST /kubernetes-clusters/ body schema confirmed |
| VastTenant CRD live | `kubectl get crd vasttenants.vast.vastdata.com -o yaml` | Cluster-scoped, name=tenant_name, default deletion=0s (but operator overrides to 300s) |
| Operator logs live | `kubectl logs -n vast-dataengine deployment/vast-operator-controller-manager` | `"deletionDelay":300` confirmed, full lifecycle traced |
| vast-telemetries-collector-config Secret | `kubectl get secret vast-telemetries-collector-config -n vast-dataengine -o json` | Schema: `v1alpha1.yaml` → CollectorConfig with per-tenant S3 creds |
| Live POST experiments | `curl` against `var203.selab.vastdata.com` | First clean POST → 201; re-POST with stale CR → 400; 5-min wait or finalizer removal → 201 again |
| `~/.kube/vastde-admin.yaml` | Local kubeconfig | ServiceAccount token giving cluster-admin access to 10.143.2.242:6443 |
