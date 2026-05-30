# VMS REST Endpoint Reference

Catalogue of every VMS REST endpoint `vastde-orch` touches (or considered touching), with attributes and purpose, validated against **VAST 5.4.3 SP4** (build `release-5.4.3-sp4-2420502`) on `var203.selab.vastdata.com`.

Each entry: **path** · **methods** · **purpose** · **request body shape** · **notes**.

---

## 🔑 Auth quick reference

| Path | Required auth | Notes |
|---|---|---|
| Most `/api/latest/*` reads & writes | HTTP Basic (cluster admin) | vastpy default |
| Tenant-scoped reads (`X-Tenant-Name: <name>`) | HTTP Basic of a manager on that tenant | cluster admin will get 403 |
| `/dataengine/setup-provisioning/` | **Bearer JWT** (no Basic) | get JWT via `/token/{tenant}/` |
| `/dataengine/` GET liveness, `/dataengine/topics`, etc. | HTTP Basic of tenant admin | |

### Get a JWT for tenant-scoped calls

```bash
POST /api/latest/token/<tenant_name>/
{"username": "<tenant-admin>", "password": "<password>"}

→ {"access": "<jwt>", "refresh": "...", "csrftoken": "..."}
```

Use it:

```
Authorization: Bearer <jwt>
```

`vastde_orch.enablement.enable._fetch_tenant_jwt()` implements this.

---

## ✅ Endpoints we use and they work

### `/tenants/` and `/tenants/{id}/`
- **Methods**: `GET`, `POST`, `PATCH`, `DELETE`
- **Purpose**: tenant CRUD.
- **Key attributes**:
  - `name` (string, required)
  - `domain_name` (string, optional)
  - `application_users_group_name` (string, **writable** — group whose members can access DataEngine on this tenant)
  - `tenant_admins_group_name` (string, writable)
  - `vippool_names` (list, **read-only** — set by creating vippools with `tenant_id` pointing here)
  - `data_engine_enabled` (string, **read-only** — toggled via `/dataengine/` endpoint, not this one)
  - `data_engine_role_enabled` (read-only)
  - `data_engine_s3_policy_enabled` (read-only)
- **Used by**: `clients/vms.py:ensure_tenant`, `enablement/tenant.py`
- **Status**: works as expected.

### `/viewpolicies/` and `/viewpolicies/{id}/`
- **Methods**: `GET`, `POST`, `PATCH`, `DELETE`
- **Purpose**: view policy (access policy) CRUD. Used to control which protocols and which clients can access a view.
- **Key attributes**:
  - `name` (string, required, unique within tenant)
  - `tenant_id` (integer)
  - `flavor` (`S3_NATIVE`, `NFS`, `MIXED`)
  - `s3_bucket_listing_groups` (list of group names — who can `s3:ListAllMyBuckets`)
  - `nfs_read_write`, `nfs_root_squash`, `s3_read_write`, `read_write` (lists of allowed clients/groups; `['*']` = all)
  - `auth_source` (`PROVIDERS`, `RPC_AND_PROVIDERS`, `RPC`)
  - `enable_access_to_snapshot_dir_in_subdirs` (bool)
- **Used by**: `clients/vms.py:ensure_viewpolicy`, `enablement/event_broker.py`
- **Status**: works.

### `/views/` and `/views/{id}/`
- **Methods**: `GET`, `POST`, `PATCH`, `DELETE`
- **Purpose**: view CRUD. A view is the protocol-agnostic data entry point.
- **Key attributes for our use**:
  - `path` (string, required, unique within tenant)
  - `policy_id` (integer, required)
  - `tenant_id` (integer, derived from policy)
  - `protocols` (list — possible: `['S3']`, `['NFS']`, `['SMB']`, `['S3', 'DATABASE']`, `['S3', 'DATABASE', 'KAFKA']`, etc.)
  - `bucket` (string, required when `S3` in protocols)
  - `bucket_owner` (string, required when `S3` in protocols, must match an existing user name)
  - `create_dir` (bool, true to create the filesystem directory)
  - `bucket_creators`, `bucket_creators_groups` (list)
  - `kafka_*` fields (encryption + auth, populated when `KAFKA` in protocols)
- **Used by**: `clients/vms.py:ensure_view`, `enablement/event_broker.py`, `enablement/source_views.py`
- **Status**: works, but our code passes `protocols=['KAFKA']` for the broker view — **must be `['S3','DATABASE','KAFKA']`** (see fix #1).

### `/users/` and `/users/{id}/`
- **Methods**: `GET`, `POST`, `PATCH`, `DELETE`
- **Purpose**: user CRUD.
- **Key attributes**:
  - `name` (string, required, unique)
  - `uid` (integer, optional but recommended)
  - `groups` (list of group names — auxiliary groups)
  - `leading_group` (string, the user's primary group)
  - `allow_create_bucket` / `allow_delete_bucket` (bool)
  - `tenant_id` (often `None` for cluster-wide users)
  - `s3_policies` (list of `{id, name}` — populated when an s3policy includes this user)
- **Used by**: `clients/vms.py:ensure_user`, `enablement/identity.py`
- **Status**: works.

### `/groups/` and `/groups/{id}/`
- **Methods**: `GET`, `POST`, `PATCH`, `DELETE`
- **Purpose**: group CRUD.
- **Key attributes**:
  - `name` (string, required)
  - `gid` (integer)
  - `local_provider` (dict — usually `{'id': 1, 'name': 'default', 'managed_by': ['SUPER_ADMIN']}` for VAST provider)
  - `s3_policies`, `s3_policies_ids` (lists — populated when an s3policy targets this group)
- **Used by**: `clients/vms.py:ensure_group`, `enablement/identity.py`
- **Status**: works.

### `/vippools/` and `/vippools/{id}/`
- **Methods**: `GET`, `POST`, `PATCH`, `DELETE`
- **Purpose**: virtual IP pool CRUD.
- **Key attributes**:
  - `name` (string, required)
  - `tenant_id` (integer — to dedicate the pool to one tenant)
  - `role` (`PROTOCOLS`, `MANAGEMENT`)
  - `subnet_cidr` (**important**: API stores **just the mask suffix** as a string, e.g. `"24"` — not `"10.30.42.0/24"`)
  - `ip_ranges` (list of `[start, end]` pairs)
- **Used by**: `clients/vms.py:ensure_vippool`, `enablement/event_broker.py`
- **Status**: works, but **our code sends full CIDR `10.30.42.0/24`** — verify VAST accepts both, or strip to just the mask.

### `/token/`, `/token/{tenant_name}/`, `/token/refresh/`
- **Methods**: `POST`
- **Purpose**: issue JWT access/refresh tokens. The `{tenant_name}` variant binds the JWT to that tenant — required for tenant-scoped endpoints like `/dataengine/setup-provisioning/`.
- **Body**: `{"username": "<user>", "password": "<pass>"}` (HTTP Basic also works on the login endpoint, but the body form is cleaner).
- **Response**: `{"access": "<jwt>", "refresh": "<jwt>", "csrftoken": "<token>"}`. The JWT TTL is short (~1 hour); use `/token/refresh/` with the refresh JWT to extend.
- **Used by**: `enablement/enable.py:_fetch_tenant_jwt`.
- **Status**: works.

### `/managers/` and `/managers/{id}/`
- **Methods**: `GET`, `POST`, `PATCH`, `DELETE`. Cluster-admin auth (no tenant header).
- **Purpose**: **VMS administrative users** (separate from filesystem users at `/users/`). Managers log into the VMS Web UI and can call tenant-scoped REST endpoints like `/dataengine/`.
- **Key attributes**:
  - `username` (string, required, unique)
  - `tenant_id` (integer, optional — None for SUPER_ADMIN, set for TENANT_ADMIN)
  - `user_type` (`SUPER_ADMIN`, `TENANT_ADMIN`)
  - `roles` (list of role IDs)
  - `first_name`, `last_name`, `is_active`, `is_default`
  - **`password` is NOT settable on POST** — call `/managers/password/` separately
- **Sub-endpoints**:
  - `PATCH /managers/password/` — set/change password. Body: `{username, password}`. Swagger does not list `password` in the schema; the live API accepts it.
  - `GET /managers/authorized_status/` — current session info
  - `PATCH /managers/{id}/unlock/` — unlock locked-out account
- **Used by**: `clients/vms.py:ensure_manager`, `set_manager_password`; `enablement/admin.py:provision_tenant_admin`.
- **Status**: works.

### `/roles/` and `/roles/{id}/`
- **Methods**: `GET`, `POST`, `PATCH`, `DELETE`. Cluster-admin auth.
- **Purpose**: RBAC role CRUD.
- **Key attributes**:
  - `name` (string, required, unique)
  - `tenant_id` (integer, optional — defines tenant scoping)
  - `ldap_groups` (list)
  - `is_admin`, `is_default`, `os_ssh_login` (bool)
  - `permissions` (list, **read-only on GET** — see `permissions_list` below)
- **`permissions_list` (UNDOCUMENTED write field on PATCH)**:
  - Discovered by grep'ing the VMS Web UI `main.*.js` for "Update Administrative Role" → `onSubmit()` sends `{name, permissions_list: [...], ldap_groups: [...]}` to `PATCH /roles/{id}/`.
  - Send as a list of perm codenames: `["create_security", "view_database", ...]`.
  - The standard tenant-admin set is **36 perms** — 9 realms × 4 actions (`create/view/edit/delete` × `applications/database/events/hardware/logical/monitoring/security/settings/support`).
  - **Without this PATCH, a newly-created role has 0 permissions** — VMS does NOT auto-populate them, contrary to what the swagger schema implies. A user with such a role gets 401 on tenant-scoped reads.
  - `create_security` covers the `apikey` resource — required for the manager to issue their own API tokens.
- **Used by**: `clients/vms.py:ensure_role` (auto-applies `STANDARD_TENANT_ADMIN_PERMISSIONS` if none specified).
- **Status**: works.

### `/realms/` and `/realms/{id}/`
- **Methods**: `GET`, `POST`, `PATCH`, `DELETE`, plus `PATCH /realms/{id}/assign/` and `PATCH /realms/{id}/unassign/`.
- **Purpose**: tenant permission realms — a namespace of objects a role can act on.
- **Key attributes**:
  - `name` (string)
  - `tenant_id` (integer)
  - `object_types` (list — e.g. `['group', 'localprovider', 'manager', 'permission', 'role', 'tenant', 'user']`)
- **Observed**: only **one** realm exists cluster-wide on this lab (`wi-realm`, id=1). Most tenant admins were created **without** custom realms (their roles use the 36 standard auto-populated permissions). So creating a realm per tenant is OPTIONAL.
- **`/realms/{id}/assign/`** — PATCH to bind a role to a realm. Body: `{role_id: <int>}`. Swagger doesn't list `role_id` but live API accepts it.
- **Used by**: `clients/vms.py:assign_role_to_realm`; `enablement/admin.py` (skipped silently when tenant has no realm).
- **Status**: works (best-effort; tolerates missing realm).

### `/s3policies/` and `/s3policies/{id}/`
- **Methods**: `GET`, `POST`, `PATCH`, `DELETE`
- **Purpose**: S3 identity policy CRUD. **This is where DataEngine permissions live** (despite the PDF calling them "identity policies").
- **Key attributes**:
  - `name` (string, required)
  - `tenant_id` (integer)
  - `enabled` (bool)
  - `groups` (list of group names — who the policy is bound to)
  - `users` (list of user names — alternate binding target)
  - `policy` (string — a JSON IAM-style policy document)
- **Sample DataEngine policy** (verbatim from `wi-tenant` s3policy id=14):
  ```json
  {
    "Id": "DataEnginePolicy<timestamp>",
    "Version": "2012-10-17",
    "Statement": [
      {
        "Sid": "DataengineTablesAccess",
        "Action": ["s3:HeadBucket","s3:Tabular*Transaction","s3:TabularList*","s3:TabularGet*","s3:TabularQueryData"],
        "Effect": "Allow",
        "Resource": ["dataengine-*", "dataengine-*/*"]
      },
      {
        "Sid": "DataEngineDefault",
        "Action": ["dataengine:CreateTrigger","dataengine:CreateFunction","dataengine:CreatePipeline"],
        "Effect": "Allow",
        "Resource": ["vast:dataengine:triggers:*","vast:dataengine:functions:*","vast:dataengine:pipelines:*"]
      }
    ]
  }
  ```
- **Used by**: (will be used by) `enablement/identity.py:attach_dataengine_policy_to_group` after fix #2.
- **Status**: works, but our current code POSTs to a fake `/identitypolicybindings/` endpoint — see fix #2.

### `/data/engine/triggers/` and `/data/engine/triggers/{id}/`
- **Methods**: `POST` (create), `PATCH` (update)
- **Purpose**: trigger CRUD within DataEngine.
- **Notes**: No `GET` exposed in swagger — listing must go through the DataEngine UI backend or the `vastde` CLI.
- **Used by**: indirectly via `vastde` CLI in `clients/vastde_cli.py`.
- **Status**: works through the `vastde` CLI shell-out.

---

## ⚠️ Endpoints that exist but behave unexpectedly

### `/eventbrokers/` and `/eventbrokers/{id}/`
- **Methods (swagger)**: `GET`, `POST`, `PATCH`, `DELETE`, plus `GET /{id}/list_topics/`
- **Purpose**: ostensibly to manage VAST Event Brokers.
- **Observed reality on this cluster**: `GET /eventbrokers/` returns `[]` even when wi-tenant has an active Kafka-enabled view. The "VAST Event Broker" appears to be **realized as a view** with `KAFKA` in `protocols`, not as a standalone resource.
- **Hypothesis**: POSTing here may create the underlying view, or the endpoint may only enumerate non-VAST brokers.
- **Status**: **avoid for now**. Create the broker via `/views/` POST with `protocols=['S3','DATABASE','KAFKA']`.

### `/kafkabrokers/` and `/kafkabrokers/{id}/`
- **Methods**: `GET`, `POST`, `PATCH`, `DELETE`, plus `GET /{id}/list_topics/`
- **Purpose**: external Kafka broker (third-party) registration on a tenant.
- **Observed**: also returns `[]` on this cluster.
- **Status**: untested. The PDF (p.12) says this is the path for "Configure Third-Party Event Broker" — our `provision_kafka_broker` calls this endpoint.

### `/topics/`, `/topics/delete/`, `/topics/rename/`, `/topics/show/`
- **Methods**: `GET`, `POST` on `/topics/`; `DELETE` on `/topics/delete/` etc.
- **Auth**: cluster-admin works; tenant-admin needs the `data-engine-<tenant>` s3policy (auto-created when DataEngine is enabled).
- **Purpose**: Kafka topic CRUD on a broker view. A "broker view" is a view with `KAFKA` in `protocols` and is treated by VAST as a Database with an auto-created `kafka_topics` schema (visible at `/database/<bucket>/schema/kafka_topics` in the Web UI).
- **Required query params**:
  - `tenant_id` (integer) — the tenant owning the broker view
  - `database_name` (string) — **the broker view's `bucket` field**, NOT the view path
- **Body for POST**: just `{"name": "<topic-name>"}` (verified live; nothing else required)
- **Body for DELETE** (on `/topics/delete/`): `{"name": "<topic-name>"}` plus the same query params
- **Response shape** (GET list):
  ```json
  {
    "count": 2,
    "next": null,
    "previous": null,
    "results": [
      {"database_name": "demo-de-broker", "name": "demo-default"},
      {"database_name": "demo-de-broker", "name": "demo-dlq"}
    ]
  }
  ```
- **Notes**: `partitions`, `retention_hours`, `compaction` mentioned in the PDF are NOT in this REST surface — those settings appear to live on the broker view itself or are managed via `/dataengine/topics` (tenant-scoped DataEngine API). Verify if these fields matter for your use case before exposing them.
- **Used by**: `clients/vms.py:ensure_topic`, `enablement/event_broker.py`.
- **Status**: works.

### `/schemas/`, `/schemas/show/`, `/schemas/delete/`, `/schemas/rename/`
- **Methods**: `GET`, `POST` on `/schemas/`; `GET` on `/schemas/show/`; etc.
- **Purpose**: schema CRUD inside a database (bucket). For Kafka-protocol broker views, the `kafka_topics` schema is auto-created.
- **Required query params**: same as `/topics/` — `tenant_id` + `database_name`.
- **GET response**:
  ```json
  [{"database_name": "demo-de-broker", "name": "kafka_topics", "properties": ""}]
  ```
- **Status**: usable; not currently called by `vastde-orch` because the Kafka schema is auto-created with the broker view.

### `/dataengine/` (API gateway — NOT the enable endpoint)
- **Methods**: `GET` returns `"Service Is Alive"` (liveness only).
- **Other methods**: POST/PATCH/PUT/DELETE all return **405 Method Not Allowed** even though the swagger lists them.
- **Auth**: tenant-scoped (X-Tenant-Name header + a manager that exists on that tenant). Cluster admin gets 403.
- **OPTIONS** reveals: this is the "Dataengine Provisioning Services Api Gateway". The real enable is at a sub-path.

### `/dataengine/setup-provisioning/` ★ — the actual enable endpoint
- **Method**: `POST`
- **Discovered**: by reading the VMS Web UI Angular bundle (`main.*.js`) — search for `setup-provisioning`. The wizard's `onSubmit()` method posts here.
- **Auth**: ★ **Bearer JWT only.** HTTP Basic is rejected with a misleading "Authorization header must contain two space-delimited values" error. The correct flow:
  1. `POST /api/latest/token/<tenant_name>/` with `{username, password}` of a tenant-admin manager → returns `{access, refresh, csrftoken}`.
  2. Send `Authorization: Bearer <access>` on this request.
- **Body** (extracted from `enablement.component.ts:onSubmit`):
  ```json
  {
    "kafka_broker": {
      "name": "<bucket-name>",     // for Internal: the broker view's bucket
      "type": "Internal",           // or "External"
      "url": "<host>"               // External only
    },
    "default_topic_name": "<topic>",
    "dead_letter_topic_name": "<topic>",
    "tenant_id": <int>,
    "kafka_ca_certificate": "<pem>"  // optional
  }
  ```
- **Success response** (HTTP 200): `{"status": "in_progress", "started_at": "...", "message": "Setup provisioning initiated", "parameters": {...}}`. The tenant's `data_engine_enabled` becomes `true` within ~seconds.
- **Common failure modes**:
  - 401 ("Invalid Api Token" / "Authentication credentials were not provided"): wrong auth scheme — must be Bearer + JWT, not Basic/Api-Token.
  - 403 ("permission_denied" inside the body): the tenant-admin role has 0 permissions. See `/roles/` → `permissions_list`.
  - 422: body validation. Check `kafka_broker` is an OBJECT not a string and `type` is `"Internal"` (capitalized).
  - 500 (HTML): typically backend-side; check K8s/Zarf are actually running.
- **Status**: ✅ **fully working** end-to-end via JWT.
- **Used by**: `enablement/enable.py:_toggle_dataengine_on_tenant` + `_fetch_tenant_jwt` + `_post_setup_provisioning`.

### `/dataengine/remove-data-engine/`
- **Method**: `DELETE`, with query param `tenant_id`.
- **Discovered**: same Angular bundle — `onActionDisableDataEngine`.
- **Purpose**: the inverse of `setup-provisioning`. Disables DataEngine on a tenant.
- **Status**: untested; the API path is known.

### `/dataengine/telemetries`
- **Methods**: `GET`, `POST`, `PATCH`, `PUT`, `DELETE`. Tenant-scoped auth required.
- **Purpose**: telemetry service status / config (corresponds to the auto-created `/dataengine-telemetries-<uuid>` view).
- **Observed**: `GET` returns `"Service Is Alive"`.
- **Status**: read-only liveness probe only; not currently used.

### `/dataengine/triggers`
- **Methods**: `GET` (list with pagination), `POST` (create), `PATCH` (update). Tenant-scoped auth required.
- **Purpose**: trigger CRUD inside DataEngine.
- **List response shape**:
  ```json
  {
    "pagination": { "next_cursor": "...", "previous_cursor": "..." },
    "data": [ <trigger>, ... ]
  }
  ```
- **Trigger object shape** (from live `wi-tenant.first-trigger`):
  ```json
  {
    "name": "first-trigger",
    "description": "",
    "tags": null,
    "type": "Element",                       // Note: capitalized; also "Schedule"
    "events": [                              // S3-style event names — NOT
      "ObjectCreated:*",                     // ElementCreated/ElementDeleted as PDF
      "ObjectRemoved:*",                     // suggests
      "ObjectTagging:Put",
      "ObjectTagging:Delete"
    ],
    "broker": {"type": "Internal", "url": "", "name": "wi-de-bucket"},
    "topic_name": "sourcetopic",             // simple string, not topic ID
    "source_bucket_name": "wi-source",       // the S3 bucket, not the VAST view path
    "config": {
      "tag_filters":  {"prefixes": [], "suffixes": []},
      "name_filters": {"prefixes": [], "suffixes": []}
    },
    "custom_extensions": null,
    "id": 7465538746260127744,
    "guid": "...",
    "tenant_guid": "...",
    "owner": {"id": "163", "id_type": "vid", "name": "wi-de-user"},
    "created_at": "2026-05-27T23:05:40.111000Z",
    "updated_at": "...",
    "vrn": "vast:dataengine:triggers:first-trigger",
    "topic": "vast:dataengine:topics:wi-de-bucket/sourcetopic",
    "status": "Ready"
  }
  ```
- **Implications for our code**:
  - The wizard's `event_type` choices (`ElementCreated`, `ElementDeleted`, `ElementTagCreated`, `ElementTagDeleted`) are **wrong** — should be S3-style: `ObjectCreated:*`, `ObjectRemoved:*`, `ObjectTagging:Put`, `ObjectTagging:Delete`.
  - The wizard's `source_view: /raw/docs` should map to **two** body fields: `broker.name` (the VAST broker view's bucket name) and `source_bucket_name` (the S3 bucket name of the watched view).
  - Refactor opportunity: replace `vastde` CLI shell-out for triggers with direct REST.
- **Status**: usable, but our trigger model needs a schema rewrite (deferred — see "Future fixes" below).

### `/dataengine/functions`
- **Methods**: `GET` (list with pagination), inferred `POST`/`PATCH`. Tenant-scoped auth required.
- **Purpose**: function (image reference) CRUD.
- **Observed shape**: empty on wi-tenant; will need a create flow to enumerate.
- **Status**: usable; our code shells out to `vastde functions ...` instead.

### `/dataengine/pipelines`
- **Methods**: `GET` (list with pagination), inferred `POST`/`PATCH`. Tenant-scoped auth required.
- **Purpose**: pipeline CRUD.
- **Observed**: empty on wi-tenant.
- **Status**: usable; currently shelled-out via `vastde pipelines ...`.

### `/dataengine/topics`
- **Methods**: `GET` (list with pagination), inferred `POST`. Tenant-scoped auth required.
- **Purpose**: topic CRUD inside DataEngine (separate from cluster-level `/topics/`).
- **Topic object shape** (from `wi-tenant.default-topic`):
  ```json
  {
    "name": "default-topic",
    "description": null,
    "broker": {"type": "Internal", "url": null, "name": "wi-de-bucket"},
    "name_in_broker": "trigger-topic",       // the actual Kafka topic name
    "default": true,
    "deadletter": false,
    "id": ..., "guid": "...", "tenant_guid": "...",
    "owner": {"id": "4294967295", "id_type": "vid", "name": "root"},
    "vrn": "vast:dataengine:topics:wi-de-bucket/trigger-topic"
  }
  ```
- **Implications**: VAST distinguishes a "topic" (logical label) from its `name_in_broker` (the actual Kafka topic). Our `ensure_topic` currently conflates these.
- **Status**: usable; replaces our broken `/topics/` calls.

---

## ❌ Endpoints we expected but they don't exist on this VAST version

| Endpoint | Status | Workaround |
|---|---|---|
| `/k8sclusters/` (and 8 spelling variants) | 404 | Use `vastde` CLI or DataEngine Web UI |
| `/containerregistries/` (and variants) | 404 | Use `vastde` CLI or DataEngine Web UI |
| `/functions/` | not in swagger | Use `vastde` CLI |
| `/pipelines/` | not in swagger | Use `vastde` CLI |
| `/identitypolicies/` / `/identitypolicybindings/` | 404 | Use `/s3policies/` |

---

## Auto-managed resources (do NOT try to create or delete)

When `data_engine_enabled` becomes `true` on a tenant, VAST auto-creates the following — they are not in our YAML and must be **excluded from any reconciliation/destroy logic**:

| Type | Identifier pattern | Note |
|---|---|---|
| View policy | `dataengine-policy` | Single per tenant |
| View policy | `vast-data-engine-telemetries-policy` | Single per tenant |
| View | `/dataengine` | Single per tenant; protocols=`['S3','DATABASE']` |
| View | `/dataengine-telemetries-<uuid>` | Single per tenant; UUID is fresh per enable |
| Default policies on tenant create | `<tenant>__default_policy`, `<tenant>__s3_default_policy` | Per-tenant defaults |

Our reconciler should detect these by name pattern and treat them as immutable.

---

## API conventions on this VAST version

- All paths under `/api/latest/`; vastpy uses `/api/<version>/` where `<version>` defaults to `latest`.
- All list endpoints accept arbitrary query params for filtering (e.g. `?name=foo`, `?tenant_id=12`).
- All resource paths end with a trailing slash; vastpy handles this automatically.
- Tenant header: `X-Tenant-Name: <name>` switches auth context to that tenant. Required for `/dataengine/` and several other tenant-scoped action endpoints. Cluster-admin tokens cannot satisfy this — the user must exist on the target tenant.
- SSL: cluster uses a self-signed cert; vastpy disables verification by default.

---

## Future fixes / refactors (deferred)

These were uncovered during the live probing but are out of scope for the current "three fixes" task:

1. **Trigger event_type values** — wizard prompts for `ElementCreated` etc.; real API expects S3-style `ObjectCreated:*`. Refactor `config/models.py:ElementTriggerSpec.event_type` and the corresponding section prompts.
2. **Trigger body fields** — `source_view` (path) needs to be split into `broker.name` (the broker view's bucket name) + `source_bucket_name` (the watched S3 bucket name).
3. **Topic schema** — distinguish `name` (label) from `name_in_broker` (actual Kafka topic). Update `ensure_topic` to pass both.
4. **Replace `vastde` CLI shell-out** — `/dataengine/triggers`, `/functions`, `/pipelines` all exist as proper REST endpoints with tenant-scoped auth. Direct REST is faster, has structured errors, and is easier to test.
5. **Tenant-scoped credential management** — `--vms-tenant-user` / `--vms-tenant-password` flags on `enable`/`apply`, plus an answers-file slot, so the operator can supply tenant-admin creds separate from the cluster-admin creds used elsewhere.

## How this file is maintained

Update each time we discover a new endpoint shape, an attribute we didn't know about, or a behavior that contradicts the PDF documentation. Treat the live VMS as the source of truth, not the PDF.
