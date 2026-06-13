# fraud-scorer

Ported from [`vast/kafka/demo-dataengine/function/`](../../) — the canonical
VAST DataEngine fraud detection demo. Stage B Phase 3 deliverable of
`docs/stage-b-prd.md`.

## What it does

Element-trigger function that scores incoming financial transactions for
fraud across 5 weighted rules and publishes results to two Kafka topics:

| Output topic | What lands there |
|---|---|
| `fraud.transactions.scored` | All processed transactions + risk score (0.0–1.0) |
| `fraud.alerts` | High-risk transactions only (score ≥ `ALERT_THRESHOLD` = 0.8) |

## Rules (see `main.py:RULE_WEIGHTS`)

| Rule | Weight | Triggers |
|---|---|---|
| `velocity` | 0.25 | >5 transactions per card in the recent window |
| `geographic` | 0.30 | Cross-region jump impossible at human travel speed |
| `amount` | 0.20 | Transaction > 4× customer avg spend |
| `card_testing` | 0.15 | Many small transactions in rapid succession |
| `fraud_ring` | 0.10 | Merchant matches known fraud-ring shells |

## Files

| File | Purpose |
|---|---|
| `main.py` | `init(ctx)` + `handler(ctx, event)` — runtime entry |
| `requirements.txt` | `confluent-kafka>=2.3.0` |
| `Aptfile` | (none) |
| `customDeps` | (none) |
| `config.example.yaml` | env-var template for local invoke |

## Env vars the function reads

| Var | Purpose | Where from |
|---|---|---|
| `KAFKA_BOOTSTRAP_SERVERS` | Broker `host:port` for output topic produce | pipeline `env:` block in the deploy YAML |

## Deploy

This function is wired into [`sample/testing/usc-fraud-pipeline.yaml`](../../sample/testing/usc-fraud-pipeline.yaml).
The deploy flow once K8s + registry are registered on the target tenant:

```bash
# 1. Build + push the image (on .74 — Mac arm64 buildpack export bug, see Phase 2)
ssh vastdata@10.143.2.74 'cd ~/vastde-orch && . .venv/bin/activate && \
  vastde functions build fraud-scorer \
    --target functions/fraud-scorer \
    --image-tag docker.selab.vastdata.com/vast-functions/fraud-scorer:dev && \
  docker push docker.selab.vastdata.com/vast-functions/fraud-scorer:dev'

# 2. Apply the pipeline (creates trigger + function record + deploys pipeline)
vastde-orch apply -c sample/testing/usc-fraud-pipeline.yaml --plan
vastde-orch apply -c sample/testing/usc-fraud-pipeline.yaml
```

## Prerequisites (on the target tenant)

- DataEngine enabled (`tenant.data_engine_enabled = True`)
- A registered K8s cluster (currently blocked on usc-tenant — see
  `experiment/pipeline-build` for the in-flight DE-API direct registration)
- A registered container registry
- A source view (`/usc-tenant/fraud-raw` per the pipeline YAML)
- The fraud-scorer image at the URL declared in the function spec
