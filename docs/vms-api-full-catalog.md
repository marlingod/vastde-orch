# VMS + DataEngine REST API — Full Catalog

Source: official Paligo HTML docs on `var203.selab.vastdata.com` (VAST 5.4.3 SP4)
- VMS V8 API: `https://var203.selab.vastdata.com/docs/en/index-en.html` — 993 endpoint sections, 621 unique endpoints
- DataEngine API: `https://var203.selab.vastdata.com/docs/en-de/index-en.html` — 229 sections, 58 endpoints
- Cross-checked against the live API on this cluster (HTTP Basic + Bearer JWT probes).

> **Companion file**: `vms-endpoints-reference.md` is our hand-curated lab notes. **This file is the authoritative reference.** Where the two disagree, this file wins.

---

## TOP FINDINGS — corrections / surprises vs. `vms-endpoints-reference.md`

1. **`/dataengine/*` is served at `/api/dataengine/`, NOT `/api/latest/dataengine/`.** The base URL example in the docs is `https://<vms-ip>/api/dataengine/<object>/`. The VMS V8 docs do NOT contain any `/dataengine/*` endpoints — DataEngine lives in a separate spec on a separate path. Our `vms-endpoints-reference.md` lists everything as `/dataengine/...` without prefixing — the actual base is `/api/dataengine/`, NOT `/api/latest/dataengine/`. (Live probe confirms.)

2. **`/api/serverless/` IS an alias for `/api/dataengine/`** — confirmed by live probe (`GET /api/serverless/setup-provisioning/` returns the same payload as `/api/dataengine/setup-provisioning/`).

3. **`POST /setup-provisioning/` does NOT accept K8s cluster or container registry inline.** Its only body fields are `kafka_broker`, `default_topic_name`, `dead_letter_topic_name`, optional `vip_pools` (list of int), optional `kafka_ca_certificate`. **K8s clusters, mTLS credentials, and container registries are SEPARATE resources** created via `POST /kubernetes-clusters/`, `POST /mtls-authentication-credentials/`, and `POST /container-registries/` AFTER setup-provisioning succeeds. The error "Failed to provision telemetries resources" therefore cannot be caused by missing k8s/registry fields in the setup body — it must be a telemetry-side issue (likely missing/insufficient VIP pool, or kafka_broker resolution failure).

4. **`vip_pools` is a top-level field on `setup-provisioning` body**, specifically labeled "VIP Pools to use for the telemetries collector". It takes a list of VIP pool **IDs (integers)**, not names. If `null`/empty, VAST picks a default — and on tenants without a dedicated PROTOCOLS pool, telemetries provisioning may fail. **This is the most likely root cause of our "Failed to provision telemetries resources" error.** Try passing `vip_pools: [<wi-vipool-id>]` explicitly.

5. **`POST /mtls-authentication-credentials/` is UNDOCUMENTED but live.** The published DE API only lists `GET /mtls-authentication-credentials/{guid}/namespaces`, yet `POST /api/dataengine/mtls-authentication-credentials/` accepts the documented `MTLSAuthenticationCredentialsCreate` schema. Live probe of empty POST returns: `{"name": "Field required", "client_key_b64": "Field required", "client_certificate_b64": "Field required", "certificate_authority_b64": "Field required"}`. So **all three certs are required, base64-encoded**, plus `name`. `tags`, `description`, `tenant_id`/`tenant_name`/`tenant_guid` are optional.

6. **`POST /kubernetes-clusters/` requires `mtls_credentials_guid`** — must be created first via `/mtls-authentication-credentials/`. **There is NO inline credential support.** Required fields: `name, kube_api_url, mtls_credentials_guid, namespaces` (list of strings, NOT a list of lists despite what the doc sample suggests — live probe of single string list works).

7. **`POST /container-registries/` requires `name, url, primary_kubernetes_cluster`**. `primary_kubernetes_cluster` is an object `{kubernetes_cluster_vrn: "vast:dataengine:kubernetes-clusters:<name>", namespace: "<ns>"}` — uses VRN, NOT GUID. `username`/`password`/`email`/`secret` are optional. `auth_type` enum is `password` (and likely `none`).

8. **`DELETE /remove-data-engine/` works, body `{"force": true}`** — confirmed live, flipped `data_engine_enabled` back to `false` on `nireny` tenant in ~5s. The doc shows `RemoveDataEngineRequest` with no documented fields, but `force` is accepted.

9. **`/topics/` POST (cluster-level) accepts MANY more fields than our reference shows**: `topic_partitions, retention_ms, message_timestamp_type` (`LogAppendTime`/`CreateTime`), `message_timestamp_before_max_ms`, `message_timestamp_after_max_ms`. Our `vms-endpoints-reference.md` says "partitions … are NOT in this REST surface" — **that is wrong**.

10. **`/views/` POST has `kafka_vip_pools`** as a top-level array of int IDs — explicit support for binding a Kafka broker view to specific VIP pools. Also has `kafka_rejoin_group_timeout_sec`, `kafka_first_join_group_timeout_sec`, `is_kafka_unencrypted_conn_allowed`, `kafka_unencrypted_auth_mechanism`, `is_kafka_encrypted_conn_allowed`, `kafka_encrypted_auth_mechanism`, `kafka_is_authorization_required`.

11. **`/managers/` and `/roles/` BOTH have `permissions_list`** in their official PATCH body schemas — it is NOT undocumented as our reference claims. It also appears in POST `/roles/`. Type in docs is `string` but the API accepts a list. (Our reference correctly says "send as a list of perm codenames".)

12. **`PATCH /managers/password`** body is just `{"password": "string"}` (NO `username` in the documented schema — that path is for the currently-authenticated user. Sending `username` may still work but is not in spec).

13. **No tenant PATCH knobs related to DE** — confirmed by reading the full `PATCH /tenants/{id}/` body schema. `data_engine_enabled` is set indirectly by hitting `/dataengine/setup-provisioning/` or `/dataengine/remove-data-engine/`. There is no `data_engine_enabled: true` PATCH path.

14. **`POST /eventbrokers/` and `POST /kafkabrokers/` have the SAME body** `{name, tenant_id, addresses: [{host, port}]}` — they are aliases (the title for both is literally "Create External Event Broker Configuration"). These are for external (third-party) Kafka brokers; the internal VAST broker is the bucket of a view with KAFKA in protocols and is created via `/views/`.

---

## Live-validation pass — 2026-05-31

These corrections come from running the full Stage A flow end-to-end on `nc-tenant` (id=16) via `scripts/test_minimal_enable.py`. Each one breaks the script (or produces a stuck/opaque state) if missed. Full diagnostic trail at `docs/research/k8s-registration-investigation-2026-05-31.md`.

15. **`POST /groups/` AND `POST /users/` REQUIRE `local_provider_id`** — not listed as required in the catalog body schemas, but live API returns `400 {"local_provider_id":["This field is required."]}`. The value to pass is the tenant's `local_provider_id` (defaults to `1` for the built-in "default" provider; read from `GET /tenants/{id}/`).

16. **`view-policy.flavor` enum is `NFS | SMB | S3_NATIVE | MIXED_LAST_WINS`** — NOT `MIXED` as the catalog originally stated. Live API: `400 — "Invalid flavor: 'MIXED', must be one of the following: SMB, NFS, MIXED_LAST_WINS, S3_NATIVE"`.

17. **DataEngine broker view (with `DATABASE` in `protocols`) REQUIRES policy `flavor: S3_NATIVE`**. Any other flavor on a view that includes DATABASE → `400 — "A view where the Database is in the list of protocols can only have a view policy where the security flavor is S3 native."` This means the single shared DE view-policy must be `S3_NATIVE`, not `MIXED_LAST_WINS`.

18. **Default + dead-letter topics MUST pre-exist in the broker bucket BEFORE setup-provisioning**. The catalog suggested setup-provisioning would auto-create them — it doesn't. Live API: `400 — "Topic 'de-default' not found on broker 'nc-de-broker'"`. Pre-create via `POST /api/latest/topics/?tenant_id=<id>&database_name=<broker-bucket>` with body `{database_name, name, topic_partitions: 16}` for each topic.

19. **`setup-provisioning.vip_pools` is effectively REQUIRED, not optional**. Omitting it lets setup-provisioning return `status: completed`, but every downstream `POST /kubernetes-clusters/` then fails with the opaque telemetries error (see #20). Always pass `vip_pools: [<protocols-pool-id>]`.

20. **`POST /kubernetes-clusters/` synchronously CREATES a cluster-scoped K8s CR** named after the tenant: `vasttenants.vast.vastdata.com/<tenant_name>`. If a CR with that name already exists, K8s returns `409 AlreadyExists` and VAST surfaces it as `400 — "Failed to provision telemetries resources in <url>"`. This is the single most common cause of stuck k8s registrations.

21. **The VAST operator has a hardcoded 300-second deletion delay** on `VastTenant` CRs (even though the CRD spec says "defaults to immediate deletion"). After `DELETE /api/dataengine/kubernetes-clusters/{guid}`, K8s shows the CR in `Deleting` state for 5 min, blocking re-registration during that window.

22. **`DELETE /api/dataengine/kubernetes-clusters/{guid}` ALSO deletes the associated mTLS credential** from VAST's DB. Re-registration after a DELETE always requires a fresh `POST /mtls-authentication-credentials/` for a new guid; the old guid will 404.

### Recovery procedure when k8s registration is stuck

Required when `POST /kubernetes-clusters/` returns `"Failed to provision telemetries resources"`:

```bash
# 1. Force-remove the stale VastTenant CR (cluster-admin kubectl on the K8s master)
kubectl get vasttenants                              # find <tenant_name>
kubectl delete vasttenant <tenant_name> --wait=false
kubectl patch vasttenant <tenant_name> --type merge -p '{"metadata":{"finalizers":null}}'
kubectl get vasttenants                              # must show: No resources found

# 2. Re-create mTLS credential if previously deleted
#    POST /api/dataengine/mtls-authentication-credentials/  with name + 3x b64 fields

# 3. Re-POST /api/dataengine/kubernetes-clusters/  (returns 201)
```

**Operational implication for the orchestrator**: before any retry of `POST /kubernetes-clusters/`, the tool should (a) check `kubectl get vasttenant <tenant_name>` on the target cluster, and (b) if present (even in `Deleting` state), run the delete + finalizer-patch sequence above. This is the only way to bypass the operator's 300s deletion delay.

---

## Authentication

| Auth | How | Used for |
|---|---|---|
| HTTP Basic | `-u user:pass` | Most `/api/latest/*` cluster-admin reads/writes |
| Basic + `X-Tenant-Name: <name>` | tenant-admin user only | Tenant-scoped `/api/dataengine/*` and `/api/serverless/*` |
| Bearer JWT (`Api-Token <token>`) | `POST /token/` or `POST /token/{tenant}/` | Same as Basic; preferred for tenant-scoped DE endpoints |
| API Token | `Authorization: Api-Token <token>` | After `POST /apitokens/` |
| mTLS | Client cert installed via `/vms/{id}/set_client_certificate/` | Optional cluster-wide |

**Confirmed live**: cluster-admin (`admin/123456`) JWT works for `/api/dataengine/*` when combined with `X-Tenant-Name`. Tenant-scoped JWT (`POST /api/latest/token/<tenant>/`) is the documented path but requires a tenant-admin password we don't have.

---

# Section A — DataEngine API  (base path: `/api/dataengine/`)

All 58 documented endpoints. Auth: tenant-scoped (X-Tenant-Name + tenant-admin, or cluster-admin JWT + X-Tenant-Name).

## A.1  /setup-provisioning/

| Verb | Path | Auth | Status |
|---|---|---|---|
| POST | `/setup-provisioning/` | tenant-JWT or cluster+tenant | works |
| GET | `/setup-provisioning/` | same | works |

### `POST /setup-provisioning/`
Body schema (`SetupProvisioningRequest`):
```json
{
  "vip_pools":           [0],                  // optional — list of VIP pool IDs for telemetries collector
  "kafka_broker": {                            // REQUIRED
    "type":  "External" | "Internal",          // REQUIRED — case-sensitive
    "url":   "https://kafka.example.com:6969", // External only (required for External)
    "name":  "my-kafka-broker"                 // REQUIRED — external broker name OR internal Bucket (View) name
  },
  "default_topic_name":     "string",          // REQUIRED
  "dead_letter_topic_name": "string",          // REQUIRED
  "kafka_ca_certificate":   "string"           // optional; base64; required if broker uses TLS
}
```
- `vip_pools` is **effectively required, not optional** (live-validated 2026-05-31). Omitting it lets setup-provisioning return `status: completed`, but every downstream `POST /kubernetes-clusters/` then fails with the opaque "Failed to provision telemetries resources" error. Always pass `vip_pools: [<protocols-pool-id>]`.
- **Default + dead-letter topics MUST pre-exist in the broker bucket** before this POST. setup-provisioning does NOT auto-create them (despite earlier catalog claims). Pre-create with `POST /api/latest/topics/?tenant_id=<id>&database_name=<broker-bucket>` body `{database_name, name, topic_partitions: 16}`.
- Response shape (200): `{status: "in_progress"|"completed"|"failed", started_at, completed_at, message, parameters: {...echoed body...}}`.

### `GET /setup-provisioning/`
Returns the current provisioning status. Response same shape as POST 200.

### Validation experiments (live)
- empty body → `400 {"kafka_broker": "Field required", "default_topic_name": "Field required", "dead_letter_topic_name": "Field required"}`
- `kafka_broker: {"type":"InvalidType","name":"x"}` → `400 {"kafka_broker": "Input should be 'External' or 'Internal'"}`
- `kafka_broker.type=Internal, name="<missing-bucket>"` → `400 {"detail": "Kafka broker '<name>' not found in VMS"}`
- `kafka_broker.type=External, name="ext", url="..."` → 200, status `in_progress`, `completed` in ~30s — no need for the bucket/view to pre-exist.

---

## A.2  /remove-data-engine/

| Verb | Path | Auth | Status |
|---|---|---|---|
| DELETE | `/remove-data-engine/` | tenant-JWT | works (confirmed live) |

Body (`RemoveDataEngineRequest`):
```json
{ "force": true }
```
Effect: flips `data_engine_enabled` back to `false`, removes auto-managed DE views/policies, drops the K8s clusters/registries/mtls creds owned by this tenant. Takes ~5s.

---

## A.3  /kubernetes-clusters/

| Verb | Path | Body schema | Status |
|---|---|---|---|
| GET | `/kubernetes-clusters/` | paginated list | works |
| POST | `/kubernetes-clusters/` | `KubernetesClusterCreate` | works |
| GET | `/kubernetes-clusters/{guid}` | — | works |
| PUT | `/kubernetes-clusters/{guid}` | `KubernetesClusterCreate` | works |
| DELETE | `/kubernetes-clusters/{guid}` | `{force: bool}` | works |

`KubernetesClusterCreate`:
```json
{
  "name":                 "string",                                   // REQUIRED
  "description":          "string",                                   // optional
  "tags":                 ["string"],                                 // optional
  "kube_api_url":         "https://k8s.example.com",                  // REQUIRED
  "mtls_credentials_guid": "204108a4-b40f-4b37-9bd9-6300628ebe15",    // REQUIRED — GUID of pre-existing mtls-credentials resource
  "namespaces":           ["insight-engine-prod", "insight-engine-dev"] // REQUIRED — list of strings
}
```
- Doc sample shows `namespaces` as `[["a","b"]]` (nested) but **live API accepts a flat list** (`["a","b"]`) — the live `kb-master` cluster has `"namespaces":["default"]`.
- The empty-body POST error confirms: `name`, `kube_api_url`, `mtls_credentials_guid`, `namespaces` are required.
- `vrn` returned: `vast:dataengine:kubernetes-clusters:<name>`
- `status` enum: `Ready`, others not documented; **commonly returns `null`** even on success (live-validated on wi-tenant kb-master + nc-tenant nc-k8s-cluster — both show `status: null` after successful registration).

**Critical side-effect (live-validated 2026-05-31):** this POST synchronously creates a cluster-scoped K8s CR `vasttenants.vast.vastdata.com/<tenant_name>` on the target K8s cluster. If a CR with the tenant's name already exists (or is in `Deleting` state — see "deletion delay" below), K8s returns 409 and VAST surfaces it as `400 — "Failed to provision telemetries resources in <url>"`.

The CR shape:
```yaml
apiVersion: vast.vastdata.com/v1
kind: VastTenant
metadata:
  name: <tenant_name>            # NOT the guid
  labels:
    vast_tenant: <tenant_guid>
spec:
  tenantGUID: <tenant_guid>
  targetNamespaces:
    - <namespace>                # matches POST body's `namespaces[0]`
```

**Deletion delay**: the VAST operator hardcodes a 300-second deletion delay on these CRs (even though the CRD's documented default is "immediate"). After `DELETE /api/dataengine/kubernetes-clusters/{guid}`, the K8s CR sits in `Deleting` state for 5 minutes — blocking re-registration during that window.

**Tied-deletion**: VAST's DELETE on a k8s cluster ALSO deletes the associated mTLS credential from VAST's DB. Re-registration always requires a fresh `POST /mtls-authentication-credentials/`; the old guid will 404.

**Recovery when stuck** (the only way to bypass the 300s deletion delay):

```bash
# On the K8s master, with cluster-admin kubectl
kubectl get vasttenants
kubectl delete vasttenant <tenant_name> --wait=false
kubectl patch vasttenant <tenant_name> --type merge -p '{"metadata":{"finalizers":null}}'
kubectl get vasttenants   # must show: No resources found
```

Then re-POST a fresh `/mtls-authentication-credentials/` followed by `/kubernetes-clusters/`. Full diagnostic trail at `docs/research/k8s-registration-investigation-2026-05-31.md`.

---

## A.4  /mtls-authentication-credentials/

| Verb | Path | Auth | Status |
|---|---|---|---|
| GET | `/mtls-authentication-credentials/` | tenant | **UNDOCUMENTED but works** — paginated list of `MTLSAuthenticationCredentials` |
| POST | `/mtls-authentication-credentials/` | tenant | **UNDOCUMENTED but works** — body `MTLSAuthenticationCredentialsCreate` |
| GET | `/mtls-authentication-credentials/{guid}/namespaces` | tenant | documented; query `kube_api_url=` required |

`MTLSAuthenticationCredentialsCreate`:
```json
{
  "name":                     "string",       // REQUIRED
  "description":              "string",       // optional
  "tags":                     ["string"],     // optional
  "client_key_b64":           "<base64-PEM>", // REQUIRED — base64-encoded client private key
  "client_certificate_b64":   "<base64-PEM>", // REQUIRED — base64-encoded X.509 client cert
  "certificate_authority_b64":"<base64-PEM>"  // REQUIRED — base64-encoded CA cert
}
```
- Empty-body POST returns: `400 {"name": "Field required", "client_key_b64": "Field required", "client_certificate_b64": "Field required", "certificate_authority_b64": "Field required"}` — proving the live endpoint exists.
- GET returns `client_key_b64: "******"` (masked) and the cert in cleartext base64.
- `vrn` returned: `vast:dataengine:mtls-authentication-credentials:<name>` (inferred from naming convention).

`MTLSAuthenticationCredentialsValidate` (schema only — endpoint not exposed in docs):
```json
{
  "kubernetes_cluster_guid": "<uuid>",
  "namespace":               "string"
}
```

---

## A.5  /container-registries/

| Verb | Path | Body | Status |
|---|---|---|---|
| GET | `/container-registries/` | paginated list | works |
| POST | `/container-registries/` | `ContainerRegistryCreate` | works |
| GET | `/container-registries/{guid}` | — | works |
| PUT | `/container-registries/{guid}` | `ContainerRegistryCreate` | works |
| DELETE | `/container-registries/{guid}` | — | works |

`ContainerRegistryCreate`:
```json
{
  "name":        "string",         // REQUIRED
  "description": "string",         // optional
  "tags":        ["string"],       // optional
  "url":         "docker.io",      // REQUIRED — registry URL (no scheme in live example)
  "primary_kubernetes_cluster": {  // REQUIRED — references an existing k8s cluster
    "kubernetes_cluster_vrn": "vast:dataengine:kubernetes-clusters:<name>",
    "namespace":              "default"
  },
  "additional_kubernetes_clusters": [   // optional — same shape as primary
    { "kubernetes_cluster_vrn": "...", "namespace": "..." }
  ],
  "auth_type": "password",         // optional enum; observed value: "password"
  "secret":    "string",           // optional
  "username":  "string",           // optional (with auth_type=password)
  "password":  "string",           // optional (with auth_type=password)
  "email":     "string"            // optional
}
```
- Empty-body POST: `400 {"name": "Field required", "url": "Field required", "primary_kubernetes_cluster": "Field required"}`.
- Live `dockerhub` registry on wi-tenant uses `url: "docker.io"` (no scheme), `auth_type: "password"`, `secret: null`.
- `vrn` returned: `vast:dataengine:container-registries:<name>`.
- Uses **VRN** (resource path), **NOT GUID**, to reference the k8s cluster.

---

## A.6  /kubernetes-secrets

| Verb | Path | Body | Status |
|---|---|---|---|
| POST | `/kubernetes-secrets` | `KubernetesSecretCreate` | works |
| DELETE | `/kubernetes-secrets` | `KubernetesSecret` | works |

`KubernetesSecretCreate`:
```json
{
  "kubernetes_cluster_vrn": "vast:dataengine:kubernetes-clusters:<name>",
  "namespace":              "insight-engine-prod",
  "secret_name":            "db-credentials",
  "entries": [
    {"key": "DB_HOST", "value": "..."},
    {"key": "DB_PASS", "value": "..."}
  ]
}
```
- For DELETE, omit `entries`.

---

## A.7  /triggers

| Verb | Path | Body | Status |
|---|---|---|---|
| GET | `/triggers` | paginated | works |
| POST | `/triggers` | `TriggerCreate` | works |
| GET/PUT/DELETE | `/triggers/{guid}` | `TriggerCreate` (PUT) | works |

`TriggerCreate`:
```json
{
  "name":        "string",
  "description": "string",
  "tags":        ["string"],
  "type":        "Element" | "Schedule",
  "events":      ["ObjectCreated:*", "ObjectRemoved:*",
                  "ObjectTagging:Put", "ObjectTagging:Delete"],
  "broker": {
    "type": "External" | "Internal",
    "url":  "...",                   // External only
    "name": "<bucket-or-broker>"
  },
  "topic_name":         "my-topic",         // topic in broker — auto-creates if missing
  "source_bucket_name": "my-bucket",        // for Element triggers — the watched S3 view bucket
  "config": {                                // shape varies by trigger type
    "tag_filters":  {"prefixes": [], "suffixes": []},
    "name_filters": {"prefixes": [], "suffixes": []}
  },
  "custom_extensions": {"key": "value"}     // optional; keys: lowercase letters+digits, start with letter, 1-20 chars
}
```
**Critical** for our code: `events` are S3-style (`ObjectCreated:*`), NOT `ElementCreated` as our YAML wizard prompts for.

---

## A.8  /functions  and  /function-revisions

| Verb | Path | Body |
|---|---|---|
| GET / POST | `/functions` | `FunctionCreate` |
| GET / PUT / DELETE | `/functions/{guid}` | `FunctionCreate` |
| GET / POST | `/function-revisions` | `FunctionRevisionCreate` |
| GET / PUT / DELETE | `/function-revisions/{guid}` | — |
| POST | `/function-revisions/{guid}/publish` | — |
| GET | `/count-functions` | — |

`FunctionRevisionCreate`:
```json
{
  "name":                  "string",
  "description":           "string",
  "tags":                  ["string"],
  "architecture":          "x86" | "arm",
  "runtime":               "python-3.6" | "python-3.7" | "python-3.8" |
                           "python-3.9" | "python-3.10" | "python-3.11",
  "artifact_type":         "image",                              // only "image" supported
  "artifact_source":       "docker.io/vast/helloworld-python",   // REQUIRED
  "image_tag":             "34",                                  // tag, NOT "latest"
  "container_registry_vrn":"vast:dataengine:container-registries:<name>", // REQUIRED
  "is_published":          false
}
```
`FunctionCreate` extends `FunctionRevisionCreate` with:
- `default_revision_number: int`
- `revision_alias: string`
- `revision_description: string`

---

## A.9  /pipelines  and  /pipeline-revisions

| Verb | Path | Body |
|---|---|---|
| GET / POST | `/pipelines` | `PipelineCreate` |
| GET / PUT / DELETE | `/pipelines/{guid}` | `PipelineCreate` |
| POST | `/pipeline/{guid}/deploy` | — (deploys to k8s) |
| GET / POST | `/pipeline-revisions` | — |
| GET / PUT / DELETE | `/pipeline-revisions/{guid}` | — |

`PipelineCreate` is `oneOf`:
- `PipelineCreateInfo` (object form):
```json
{
  "name":                  "string",
  "description":           "string",
  "tags":                  ["string"],
  "kubernetes_cluster_vrn":"vast:dataengine:kubernetes-clusters:<name>",
  "namespace":             "string",
  "manifest": {
    "triggers":            [{}],
    "function_deployments":[{}],
    "links":               [{}]
  }
}
```
- OR a string: **raw manifest text (YAML or JSON)**.

---

## A.10  /topics  (DataEngine topics, NOT cluster-level `/topics/`)

| Verb | Path | Body |
|---|---|---|
| GET / POST | `/topics` | `TopicCreate` |
| GET / PUT / DELETE | `/topics/{guid}` | `TopicCreate` |

Topic body shape (from live `wi-tenant.default-topic`):
```json
{
  "name":            "default-topic",                       // logical label
  "description":     null,
  "broker":          {"type": "Internal", "name": "<bucket>"},
  "name_in_broker":  "trigger-topic",                       // actual Kafka topic name (may differ)
  "default":         true,
  "deadletter":      false
}
```

---

## A.11  /v1/logs, /v1/traces, /traces, /logs, /trace-tree, /span-logs

Telemetries query endpoints — read-only OpenTelemetry data. Base URLs:
- Ingest: `http://vms-ip/api/dataengine/telemetries`
- Query:  `http://vms-ip/api/dataengine/query_telemetries`

| Verb | Path | Purpose |
|---|---|---|
| POST | `/v1/logs` | OTEL gRPC logs ingest |
| POST | `/v1/traces` | OTEL gRPC traces ingest |
| GET | `/traces` | query traces |
| GET | `/logs` | query logs |
| GET | `/trace-tree` | tree of spans |
| GET | `/span-logs` | logs under a span |

## A.12  /dashboard/*

| Verb | Path |
|---|---|
| GET | `/dashboard/stats` |
| GET | `/dashboard/events-stats` |
| GET | `/dashboard/execution-time` |

---

# Section B — VMS V8 API (base path: `/api/latest/`)

621 documented endpoints across 100+ resource families. Below: the families relevant to vastde-orch.

## B.1  /tenants/

| Verb | Path | Purpose |
|---|---|---|
| GET | `/tenants/`, `/tenants/{id}/` | list / read |
| POST | `/tenants/` | create |
| PATCH | `/tenants/{id}/` | modify |
| DELETE | `/tenants/{id}/` | delete |
| POST | `/tenants/{id}/is_operation_healthy` | dry-run a PATCH |
| PATCH | `/tenants/{id}/client_ip_ranges/` | manage allowed client IPs |
| PATCH | `/tenants/{id}/client_metrics/` | per-tenant client metrics |
| POST | `/tenants/{id}/{revoke,deactivate,reinstate,rotate}_encryption_group/` | encryption group ops |

`POST /tenants/` body — fields relevant to DE:
- `name` (required)
- `client_ip_ranges`: `[[start, end], ...]`
- `posix_primary_provider`, `login_name_primary_provider`: `NONE` / `LDAP` / `AD` / ...
- `*_provider_id`: ad, ldap, nis, local, oidc, krb (integers, default local=1)
- `tenant_admins_group_name`: string
- `application_users_group_name`: **the group whose members can access DataEngine on this tenant** (writable on POST and PATCH)
- `default_others_share_level_perm`: `READ` / `FULL` / ...
- QoS, capacity rules, SMB settings — see body sample
- **No `data_engine_*` field** on POST or PATCH — these are toggled via `/dataengine/setup-provisioning/` and `/dataengine/remove-data-engine/`.

Read-only on GET: `data_engine_enabled`, `data_engine_role_enabled`, `data_engine_s3_policy_enabled`, `vippools` (list of `{id,name}`), `vippool_names`, `guid`.

---

## B.2  /managers/

| Verb | Path | Purpose |
|---|---|---|
| GET | `/managers/`, `/managers/{id}/` | list / read |
| POST | `/managers/` | create |
| PATCH | `/managers/{id}/` | modify |
| DELETE | `/managers/{id}/` | delete |
| PATCH | `/managers/{id}/unlock/` | unlock |
| PATCH | `/managers/password` | change current user password |
| GET | `/managers/authorized_status/` | session info |

`POST /managers/`:
```json
{
  "username": "string",
  "password": "string",
  "first_name": "string", "last_name": "string",
  "roles": [0],                              // role IDs
  "is_temporary_password": true,
  "password_expiration_disabled": true,
  "tenant_id": 0,                            // null for SUPER_ADMIN
  "user_type": "SUPER_ADMIN" | "TENANT_ADMIN"
}
```
Contrary to our reference, `password` IS in the documented POST schema — no separate password call needed at creation time. Reference's claim that "password is NOT settable on POST" is **wrong**.

`PATCH /managers/{id}/`:
```json
{
  "username": "string", "password": "string",
  "first_name": "string", "last_name": "string",
  "roles": [0],
  "realm": "string",
  "permissions": "string",
  "permissions_list": ["string"],      // list of permission codenames
  "object_type": "string", "object_id": 0,
  "is_temporary_password": true, "password_expiration_disabled": true,
  "user_type": "SUPER_ADMIN"
}
```

`PATCH /managers/password`: `{"password": "string"}` — operates on the currently-authenticated user.

---

## B.3  /roles/

| Verb | Path |
|---|---|
| GET / POST | `/roles/` |
| GET / PATCH / DELETE | `/roles/{id}/` |
| GET | `/roles/realm_object_types/` (list of allowed realm object types) |

`POST /roles/`:
```json
{
  "name":             "string",
  "ldap_groups":      ["string"],
  "realm":            "string",
  "permissions":      "view",
  "permissions_list": "create_applications",   // docs say string, live accepts list
  "object_type":      "string",
  "object_id":        0,
  "tenant_id":        0,
  "tenant_ids":       [0]
}
```
`permissions_list` IS documented; our reference's claim of it being "UNDOCUMENTED" is **wrong**.

---

## B.4  /realms/

| Verb | Path |
|---|---|
| GET / POST | `/realms/` |
| GET / PATCH / DELETE | `/realms/{id}/` |
| PATCH | `/realms/{id}/assign/` (body `{object_type: string}`) |
| PATCH | `/realms/{id}/unassign/` (same) |

`POST /realms/`:
```json
{ "name": "string", "object_types": ["string"], "tenant_id": 0 }
```

Note: docs show `/realms/{id}/assign/` body as `{"object_type": "string"}`, NOT `{"role_id": <int>}` as our reference claims.

---

## B.5  /users/

| Verb | Path | Purpose |
|---|---|---|
| GET / POST | `/users/` | list / create local user |
| GET / PATCH / DELETE | `/users/{id}/` | read / modify / delete |
| PATCH | `/users/{id}/tenant_data/` | per-tenant override |
| POST / PATCH / DELETE | `/users/{id}/access_keys/` | S3 access keys (local) |
| POST / PATCH / DELETE | `/users/non_local_keys/` | S3 access keys (non-local, e.g. LDAP-backed) |
| POST | `/users/copy/` | copy users between local providers |
| PATCH | `/users/query/` | modify non-local user |
| PATCH | `/users/refresh/` | refresh user data |

`POST /users/`:
```json
{
  "name":               "string",
  "uid":                0,
  "leading_gid":        0,
  "gids":               [0],
  "local":              true,
  "allow_create_bucket":true,
  "allow_delete_bucket":true,
  "s3_superuser":       true,
  "s3_policies_ids":    {},
  "password":           "string",
  "tenant_id":          0,    // REQUIRED for tenant-scoped users
  "local_provider_id":  0     // REQUIRED (live-validated 2026-05-31; catalog originally
                              //   marked optional). Use the tenant's `local_provider_id`,
                              //   default = 1.
}
```

---

## B.6  /groups/

`POST /groups/`:
```json
{
  "name":               "string",
  "gid":                0,
  "sid":                "string",
  "s3_policies_ids":    {},
  "tenant_id":          0,   // REQUIRED for tenant-scoped groups
  "local_provider_id":  0    // REQUIRED (live-validated 2026-05-31). Default = 1.
}
```
Plus `PATCH /groups/query/` for non-local groups (LDAP/AD).

**GID uniqueness is enforced per-local-provider, NOT per-tenant.** A group with the same gid in any tenant on the same `local_provider_id` will collide: `400 — "Group with the same gid already exists in that local provider."` Pick a gid not already in use across the whole cluster.

---

## B.7  /vippools/

`POST /vippools/`:
```json
{
  "name":         "string",
  "start_ip":     "string",
  "end_ip":       "string",
  "subnet_cidr":  0,           // integer mask (NOT full CIDR string)
  "subnet_cidr_ipv6": 0,
  "vlan":         0,
  "gw_ip":        "string", "gw_ipv6": "string",
  "cnode_ids":    "string", "cnode_names": "string",
  "cluster_id":   0,
  "domain_name":  "string",
  "role":         "PROTOCOLS" | "MANAGEMENT",
  "ip_ranges":    [["start", "end"], ...],
  "tenant_id":    0,
  "vms_preferred":true,
  "enabled":      true,
  "port_membership": "right",
  "enable_l3":    true,
  "bgp_config_id":0,
  "vast_asn":     0, "peer_asn": 0,
  "enable_weighted_balancing": true,
  "client_monitoring_ips": [["ip"]]
}
```
**`subnet_cidr` is an integer (the mask number e.g. 24), NOT a full CIDR string**. Our reference notes this correctly.

---

## B.8  /views/

`POST /views/` — fields relevant to DataEngine:
```json
{
  "name":         "string",          // optional unless used
  "path":         "/some/path",      // REQUIRED, unique within tenant
  "alias":        "string",
  "bucket":       "string",          // REQUIRED when S3 in protocols
  "policy_id":    0,                 // REQUIRED — view-policy ID
  "tenant_id":    0,
  "protocols":    ["S3"|"NFS"|"SMB"|"DATABASE"|"KAFKA"|"NFS4"|"BLOCK"], // list
  "bucket_owner": "string",          // REQUIRED for S3
  "bucket_owner_type": "USER"|"GROUP",
  "bucket_creators": ["user"],
  "bucket_creators_groups": ["group"],
  "create_dir":   true,
  "owner":        "string", "owner_type": "posix"|"nfs"|"smb",
  "owning_group": "string", "owning_group_type": "posix"|"nfs"|"smb",

  /* KAFKA fields */
  "kafka_vip_pools":           [0],   // list of VIP pool IDs
  "kafka_rejoin_group_timeout_sec":      0,
  "kafka_first_join_group_timeout_sec":  0,
  "is_kafka_unencrypted_conn_allowed":   true,
  "kafka_unencrypted_auth_mechanism":    "NONE"|"PLAIN"|"SCRAM-SHA-256"|"SCRAM-SHA-512",
  "is_kafka_encrypted_conn_allowed":     true,
  "kafka_encrypted_auth_mechanism":      "NONE"|"PLAIN"|"SCRAM-SHA-256"|"SCRAM-SHA-512",
  "kafka_is_authorization_required":     true,

  /* other */
  "s3_versioning": true, "s3_unverified_lookup": true,
  "locking": true, "s3_locks_retention_mode": "NONE"|"GOVERNANCE"|"COMPLIANCE",
  "is_seamless": true,
  "share_acl": {"enabled": true, "acl": [{}]},
  "abe_protocols": ["NFS"|"SMB"], "abe_max_depth": 0,
  "qos_policy_id": 0, "qos_policy": "string"
}
```

Internal Kafka broker = a view with `KAFKA` in protocols. Use the **view's `bucket` value** as the `kafka_broker.name` in setup-provisioning.

---

## B.9  /viewpolicies/

Long body — key DE-relevant fields:
- `name`, `flavor` (`NFS`|`SMB`|`S3_NATIVE`|`MIXED_LAST_WINS` — NOT `MIXED`; live-validated 2026-05-31), `cluster_id`, `tenant_id`
- **For the DataEngine broker view (S3+DATABASE+KAFKA), `flavor` MUST be `S3_NATIVE`.** Any other flavor → `400 — "A view where the Database is in the list of protocols can only have a view policy where the security flavor is S3 native."`
- `auth_source` (`RPC`|`PROVIDERS`|`RPC_AND_PROVIDERS`)
- `vip_pools`: list of int (pool IDs)
- per-protocol RW/RO arrays: `nfs_read_write`, `s3_read_write`, etc.
- `nfs_root_squash`, `nfs_no_squash`, `nfs_all_squash`
- `s3_bucket_full_control`, `s3_object_acl`, `s3_bucket_acl`
- `protocols_audit` (audit settings sub-object)
- `s3_visibility`, `s3_visibility_groups` (bucket listing)
- `s3_bucket_listing_groups` (control who can `s3:ListAllMyBuckets`)
- `nfs_minimal_protection_level`: `NONE`|`SYS`|`KRB5`|`KRB5I`|`KRB5P`

Sub-actions:
- `PATCH /viewpolicies/{id}/refresh_netgroups/`
- `POST /viewpolicies/{id}/remote_mapping/` body `{peer, remote_policy}`
- `DELETE /viewpolicies/{id}/remote_mapping/` body `{peer}`

---

## B.10  /s3policies/

| Verb | Path |
|---|---|
| GET / POST | `/s3policies/` |
| GET / PATCH / DELETE | `/s3policies/{id}/` |

`POST /s3policies/`:
```json
{
  "name":      "string",
  "policy":    "<JSON IAM document as string>",
  "tenant_id": 0
}
```
The `policy` field is a JSON string (escape your inner JSON). This is where DataEngine permissions live (sample in `vms-endpoints-reference.md`).

`PATCH /s3policies/{id}/`: `{name, policy, enabled}`.

---

## B.11  /iamroles/

| Verb | Path |
|---|---|
| GET / POST | `/iamroles/` |
| GET / PATCH / DELETE | `/iamroles/{id}/` |
| GET | `/iamroles/{id}/credentials` — get STS creds |
| PATCH | `/iamroles/{id}/revoke_access_keys` |

`POST /iamroles/`:
```json
{
  "name":         "string",
  "description":  "string",
  "tenant_id":    0,
  "trust_policy": "<JSON trust policy as string>",
  "s3_policies":  [0]
}
```

---

## B.12  /token/

| Verb | Path | Body |
|---|---|---|
| POST | `/token/` | `{username, password}` → `{access, refresh, csrftoken}` |
| POST | `/token/refresh/` | `{refresh}` → `{access, refresh}` |

Both: no auth required.

The path `/token/{tenant_name}/` is NOT in the docs but **does exist** on the live cluster (confirmed previously by our reference); it returns a JWT scoped to that tenant.

---

## B.13  /eventbrokers/  and  /kafkabrokers/

**Both have IDENTICAL body schemas and titles** — they are aliases for "Create External Event Broker Configuration":

```json
{
  "name":      "string",
  "tenant_id": 0,
  "addresses": [{"host": "string", "port": 0}]
}
```

Plus `GET /{id}/list_topics/` for both.

The internal VAST event broker is realized as a view with `KAFKA` in `protocols` — not via these endpoints. These endpoints are for registering EXTERNAL Kafka brokers (third-party) on a tenant.

---

## B.14  /topics/  (cluster-level Kafka topics)

| Verb | Path | Body |
|---|---|---|
| GET / POST | `/topics/` | see below |
| PATCH | `/topics/` | partial update |
| GET | `/topics/show/` | detail |
| PATCH | `/topics/rename/` | rename |
| DELETE | `/topics/delete/` | delete |

`POST /topics/`:
```json
{
  "database_name":                  "string",            // the broker view's bucket name (NOT view path)
  "name":                           "string",
  "topic_partitions":               0,
  "message_timestamp_type":         "LogAppendTime" | "CreateTime",
  "retention_ms":                   0,
  "message_timestamp_before_max_ms":0,
  "message_timestamp_after_max_ms": 0
}
```
**This contradicts `vms-endpoints-reference.md`** which says `partitions`/`retention_hours`/`compaction` are NOT in this surface. `topic_partitions` and `retention_ms` ARE — `retention_ms` (milliseconds), not hours.

Required query params (live): `tenant_id`, `database_name`.

`DELETE /topics/delete/`:
```json
{
  "database_name":   "string",
  "schema_name":     "string",
  "name":            "string",
  "tenant_id":       0,
  "is_imports_table":true
}
```

---

## B.15  /schemas/

| Verb | Path | Body |
|---|---|---|
| POST | `/schemas/` | `{database_name, name, tenant_id}` |
| GET | `/schemas/show/` | detail |
| PATCH | `/schemas/rename/` | `{database_name, name, new_name, tenant_id}` |
| DELETE | `/schemas/delete/` | `{database_name, name, tenant_id}` |

---

## B.16  /certificates/

| Verb | Path |
|---|---|
| GET / POST | `/certificates/` |
| GET / PATCH / DELETE | `/certificates/{id}/` |

`POST /certificates/`:
```json
{
  "name":           "string",
  "certificate":    "<PEM>",
  "private_key":    "<PEM>",
  "ca_certificate": "<PEM>",
  "cert_type":      "WEBHOOK" | "KAFKA"
}
```
**`cert_type` enum has ONLY `WEBHOOK` and `KAFKA`** — there is no MTLS option here. mTLS certs for DataEngine are uploaded via the (undocumented but live) `/api/dataengine/mtls-authentication-credentials/` endpoint, NOT here.

---

## B.17  Other relevant families

| Family | Endpoints | Notes |
|---|---|---|
| `/apitokens/` | GET / POST list+create; PATCH revoke; GET single | manage API tokens (`Api-Token <token>` header) |
| `/login/`, `/logout/` | POST | session-based auth |
| `/localproviders/` | full CRUD | local identity provider |
| `/ldaps/`, `/oidcs/`, `/kerberos/`, `/nis/`, `/activedirectory/` | full CRUD | external auth providers |
| `/qospolicies/` | full CRUD | QoS policy |
| `/userquotas/`, `/quotas/` | full CRUD | quotas |
| `/protectionpolicies/`, `/protectedpaths/`, `/snapshots/`, `/globalsnapstreams/`, `/replicationtargets/`, `/replicationstreams/` | full CRUD | data protection |
| `/encryptedpaths/`, `/encryptiongroups/` | full CRUD | encryption |
| `/clusters/{id}/set_certificates/` | POST | EKM certs |
| `/vms/{id}/set_client_certificate/` | PATCH | install VMS client cert (mTLS at the VMS layer) |
| `/webhooks/`, `/callhomeconfigs/` | full CRUD | notifications |
| `/alarms/`, `/events/`, `/eventdefinitions/` | mostly GET | alarms & events |
| `/permissions/` | GET | list available permission codenames (use to build `permissions_list`) |
| `/tables/`, `/columns/`, `/projections/`, `/projectioncolumns/` | full CRUD | VAST DB tables |
| `/bigcatalogconfig/`, `/bigcatalogindexedcolumns/` | CRUD | big-catalog |
| `/vastdb/`, `/vastdbtable/` | special | VAST DB utilities |

No `/databases/` or `/datadbs/` family — VAST DB tables are managed via `/tables/` and `/schemas/` keyed by `database_name = <view bucket>`.

---

# Section C — Likely root cause of "Failed to provision telemetries resources"

Based on the schema findings + live probes:

1. **`/dataengine/setup-provisioning/` does NOT take k8s_cluster or container_registry inline.** Our code's failure trying to register a k8s cluster + registry via setup-provisioning is a category error — those go in separate POSTs AFTER setup-provisioning succeeds.

2. **The setup-provisioning POST has an optional `vip_pools: [int]` field** — explicitly "VIP Pools to use for the telemetries collector". If the tenant has multiple VIP pools (or none with the right role), the implicit selection fails with "Failed to provision telemetries resources".
   - Live test on `nireny` (tenant with NO `PROTOCOLS` pool) → setup with `External` broker succeeded. So the failure is **probably tenant- and Kafka-broker-specific**.
   - **Fix to try**: explicitly pass `vip_pools: [<wi-vipool.id>]` on setup-provisioning POST.

3. **For Internal broker, the named view bucket must exist BEFORE the setup-provisioning POST.** If the view bucket is missing or unreachable, the telemetries setup (which creates a Kafka topic IN that broker) fails. The error wording "telemetries resources" is misleading — it's the broker resolution.

4. **Correct order of operations**:
   1. Create tenant.
   2. Create VIP pool with `tenant_id = <tenant.id>` and `role = PROTOCOLS`.
   3. Create user/group for DE (`application_users_group_name` on the tenant).
   4. Create view-policy.
   5. Create the broker view (`/views/` with `protocols=['S3','DATABASE','KAFKA']`, `kafka_vip_pools=[<pool.id>]`).
   6. **`POST /api/dataengine/setup-provisioning/`** with `kafka_broker={type:Internal, name:<broker.bucket>}`, the two topic names, and `vip_pools=[<pool.id>]`.
   7. POST mTLS credentials → POST k8s cluster (uses credentials guid) → POST container registry (uses cluster vrn).
   8. THEN create functions / pipelines / triggers.

5. **For teardown**: `DELETE /api/dataengine/remove-data-engine/ body {"force": true}` undoes step 6 cleanly. Confirmed live.
