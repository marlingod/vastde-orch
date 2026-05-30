# Reference: A fully DataEngine-enabled tenant (wi-tenant on var203.selab.vastdata.com)

Captured **2026-05-28** by introspection of `wi-tenant` (id=12), which has `data_engine_enabled = True`. This is the **happy state model** that `vastde-orch enable` is supposed to produce.

## The seven-resource pattern

When DataEngine is enabled on a tenant, the cluster ends up with these resources:

### 1. Tenant (modified)
```
data_engine_enabled            = True
application_users_group_name   = 'wi-group'          # ← who has DE access
vippool_names                  = ['wi-vipool']        # ← dedicated VIPs
```

### 2. VIP Pool (dedicated to the tenant)
```
id=17  name='wi-vipool'
role='PROTOCOLS'
subnet_cidr='24'
ip_ranges=[['172.200.203.135', '172.200.203.137']]
```

### 3. Group (the DataEngine user group)
```
id=3  name='wi-group'  gid=1209
s3_policies=[
  {id:14, name:'data-engine-wi-tenant'},
  {id:15, name:'wi-de-identity-policy'},
]
```

### 4. User (bucket owner for the broker view)
```
id=11  name='wi-de-user'
groups=['wi-group']
allow_create_bucket=True  allow_delete_bucket=True
```

### 5. View policies (six total — three auto, three operator-created)
| id | name | flavor | created by |
|---|---|---|---|
| 33 | `wi-tenant__s3_default_policy` | S3_NATIVE | **auto** at tenant creation |
| 34 | `wi-tenant__default_policy` | NFS | **auto** at tenant creation |
| 35 | `wi-s3-policy` | S3_NATIVE | operator |
| 36 | `wi-nfs-policy` | NFS | operator |
| 37 | `dataengine-policy` | S3_NATIVE | **auto** when DE enabled (the `/dataengine` view's policy) |
| 38 | `vast-data-engine-telemetries-policy` | S3_NATIVE | **auto** when DE enabled |

### 6. Views (four total)
| id | path | protocols | bucket | owner | notes |
|---|---|---|---|---|---|
| 253 | `/wi-de-broker` | **S3, DATABASE, KAFKA** | `wi-de-bucket` | `wi-de-user` | **the event broker view** |
| 254 | `/dataengine` | S3, DATABASE | — | — | **auto-created** |
| 255 | `/dataengine-telemetries-<uuid>` | S3, DATABASE | — | — | **auto-created** |
| 260 | `/wi-sources3` | S3 | — | — | a source view for triggers |

### 7. S3 identity policies (where DataEngine permissions live)

Two policies, both bound to `wi-group`:

```json
{
  "Id": "DataEnginePolicy1779850499",
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DataengineTablesAccess",
      "Action": [
        "s3:HeadBucket",
        "s3:Tabular*Transaction",
        "s3:TabularList*",
        "s3:TabularGet*",
        "s3:TabularQueryData"
      ],
      "Effect": "Allow",
      "Resource": ["dataengine-*", "dataengine-*/*"]
    },
    {
      "Sid": "DataEngineDefault",
      "Action": [
        "dataengine:CreateTrigger",
        "dataengine:CreateFunction",
        "dataengine:CreatePipeline"
      ],
      "Effect": "Allow",
      "Resource": [
        "vast:dataengine:triggers:*",
        "vast:dataengine:functions:*",
        "vast:dataengine:pipelines:*"
      ]
    }
  ]
}
```

Identical to the PDF p.27 spec.

## Critical fact: there is no `/eventbrokers/` resource

The PDF talks about "VAST Event Broker" as if it were a thing. In reality:

- A "VAST Event Broker" **is just a view** with `KAFKA` in its `protocols` list.
- The `/eventbrokers/` endpoint exists in swagger (`GET`, `POST`) — but `client.eventbrokers.get()` returns `[]` on this cluster despite an active Kafka-enabled view. The endpoint may write the underlying view directly or may be a façade.
- POSTing to `/views/` with `protocols=['S3','DATABASE','KAFKA']` and the right `bucket`/`bucket_owner` is likely the operative call.

## Gaps between this reality and our current code

| What we have | What's actually true | Fix |
|---|---|---|
| `vms.py:ensure_view(..., protocols=['KAFKA'])` | Need **`['S3', 'DATABASE', 'KAFKA']`** — KAFKA alone is rejected | Update `enablement/event_broker.py` |
| `vms.py:ensure_topic(...)` calls `/topics/` | `/topics/` requires `database_name` param; topics may not be a separate resource | Investigate further — likely managed inside the view |
| `enablement/identity.py:attach_dataengine_policy_to_group` posts to `/identitypolicybindings/` | Real endpoint is `/s3policies/` with `groups: ['wi-group']` field | Rewrite to POST to `/s3policies/` with full policy doc |
| Our code doesn't expect `/dataengine` and `/dataengine-telemetries-*` views | They appear automatically when DE is enabled on tenant | Either filter them out of any "destroy" reconciliation, or treat them as immutable system resources |
| `vippool.subnet_cidr` we send as `10.30.42.0/24` | API stores just `'24'` (the mask only) | Change to send the mask suffix only, or test that both are accepted |
| Identity policy attached to group via `groups: ['wi-group']` field on `/s3policies/` POST | Already partially right in PDF, but our code uses the wrong URL | One unified POST with the JSON policy doc + groups list |

## What this means for `vastde-orch enable`

Today the orchestrator would (assuming we skip k8s/registry):
- ✅ Create tenant
- ✅ Create vippool (likely works, possibly with field name nit)
- ✅ Create viewpolicy
- ❌ Create the broker view with `['KAFKA']` only — needs `['S3','DATABASE','KAFKA']`
- ❌ Create topics via `/topics/` POST — unclear if this is the right surface
- ✅ Create group + users
- ❌ Attach identity policy — wrong endpoint
- ✅ Toggle `data_engine_enabled = True` on the tenant (PATCH the tenant)
- ❌ Auto-created views (`/dataengine`, `/dataengine-telemetries-*`) appear — we don't currently expect them

So **two genuine fixes** are needed before `enable` will work against this VAST version:
1. **broker view protocols** → `['S3', 'DATABASE', 'KAFKA']`
2. **identity policy endpoint** → `/s3policies/` POST with `groups: [...]` and a `policy: <json string>` field

Topic creation is the third unknown; we'd need to do a single test POST against a disposable broker view to confirm the API.

## Useful reconnaissance commands

```bash
# Full tenant schema:
.venv/bin/python -c "
import os, json
from dotenv import load_dotenv
from vastpy import VASTClient
load_dotenv('.env')
c = VASTClient(address=os.environ['VMS_ADDRESS'],
               user=os.environ['VMS_USER'], password=os.environ['VMS_PASSWORD'])
print(json.dumps(c.tenants.get(id=12)[0], indent=2, default=str))
"

# Find the Kafka-enabled view of any tenant:
.venv/bin/python -c "
import os
from dotenv import load_dotenv
from vastpy import VASTClient
load_dotenv('.env')
c = VASTClient(address=os.environ['VMS_ADDRESS'],
               user=os.environ['VMS_USER'], password=os.environ['VMS_PASSWORD'])
for v in c.views.get() or []:
    if 'KAFKA' in (v.get('protocols') or []):
        print(v['tenant_name'], '→', v['path'], '(bucket', v.get('bucket'), ')')
"
```
