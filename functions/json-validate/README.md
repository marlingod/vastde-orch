# json-validate

VAST DataEngine function — canonical scaffold from `vastde functions init python-pip`.
Validates uploaded JSON, writes S3 tags. Phase 1 deliverable of `docs/stage-b-prd.md`.

## Files
| File | Purpose |
|---|---|
| `main.py` | `init(ctx)` + `handler(ctx, event)` |
| `requirements.txt` | `boto3` |
| `Aptfile` | (empty) |
| `customDeps` | (empty) |
| `cloudevent.yaml` | local-test fixture |
| `config.yaml` | env vars for local + deployed |

## Build + push
```bash
vastde functions build json-validate
vastde functions push  json-validate         # or `docker push <image>`
```

## Local run
```bash
vastde functions localrun json-validate -c config.yaml
curl -X POST http://localhost:8080/ \
  -H "Content-Type: application/cloudevents+yaml" \
  --data-binary @cloudevent.yaml
```
