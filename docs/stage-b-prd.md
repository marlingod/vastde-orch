# Stage B PRD: First real pipeline, end-to-end on usc-tenant

**Status:** draft · 2026-06-07
**Owner:** Yemalin
**Target:** working DataEngine pipeline running on `usc-tenant`, var203

---

## Problem

`vastde-orch apply` and the `pipelines/triggers/functions` modules have been
built and tested at unit level — but **no pipeline has ever been deployed
end-to-end** on this toolchain. The only sample (`sample/demo_tenant.yaml`)
is annotated *"Read-only test pipeline; never deployed in this session."*
No real function code exists in the repo. Until we ship one, we don't know:

- whether `vastde-orch apply` survives a real deploy against live VAST
- whether `function build` / image push works end-to-end with the chosen registry
- whether triggers actually fire on real S3 events on a source view
- which gaps exist in the trigger schema, function schema, or flow validator
  that only show up under real-world inputs

## Goal

**One working pipeline deployed to `usc-tenant` that processes a real S3
event end-to-end.** The pipeline doesn't need to be useful — it needs to be
a credible proof point that the toolchain works against a live cluster, so
the team can show it to field SEs.

## Success criteria

1. `vastde-orch apply -c sample/testing/usc-pipeline.yaml` succeeds, no manual VMS clicks
2. Uploading an object to the source view triggers the function via Knative
3. The function logs its execution (so we can verify it ran)
4. Re-running `apply` reports `unchanged` (true idempotency)
5. `vastde-orch destroy -c sample/testing/usc-pipeline.yaml` cleanly tears it down

## Out of scope

- Production-grade error handling, retries, DLQ wiring (the broker already has a DLQ topic; we'll exercise it in a follow-up)
- Multiple functions, complex flow DAGs (one trigger → one function for v1)
- Function code beyond the minimum needed to demonstrate execution + logging
- Auto-scaling tuning, resource limits, K8s observability
- CI integration of `function build` / `apply`

## Pick a use case (need one before building)

Three options, ranked by simplicity:

| | Use case | Trigger | Function does | Why pick |
|---|---|---|---|---|
| **A** | **PDF text extract** | new `.pdf` in `/usc-tenant/raw-pdfs` | runs `pdftotext`, writes `.txt` next to it | Matches the demo_tenant placeholder; common SE-demo pattern |
| **B** | **Image thumbnail** | new `.jpg/.png` in a source view | resize to 200x200, write to a sibling key | Tiny code (~20 lines using Pillow), fast to build |
| **C** | **JSON validate-and-tag** | any object in a source view | parse JSON, write tags back via S3 metadata | No new dependencies; entire function is stdlib |

**Recommendation: C** — minimal blast radius, fastest to build, exercises every
moving part (S3 read, write, tagging) without dragging in PDF or image libs
that may have container-build surprises.

## Existing surface to lean on

| What | Where |
|---|---|
| Apply command | `src/vastde_orch/cli.py:apply` (already handles `--plan`, `--interactive`, `--only`, `--no-deploy`) |
| Reconciler | `src/vastde_orch/pipelines/pipelines.py:ensure_pipeline` |
| Trigger + function reconciler | `pipelines/triggers.py:ensure_trigger`, `pipelines/functions.py:ensure_function` |
| Image build/push | `pipelines/functions.py:compute_image_tag` + `vastde-orch function build <name>` |
| vastde CLI wrappers | `clients/vastde_cli.py` — `triggers_{list,create,update,delete}`, same for functions/pipelines |
| Schema | `config/models.py:{PipelineSpec, ElementTriggerSpec, FunctionSpec, FlowEdge}` |
| Pre-existing test | `tests/test_pipelines.py` |

Nothing to build new at the orchestrator level. **All work is in the artifact
layer**: function code, container image, sample yaml, source view, and the
verification harness.

## Implementation plan (phased — each phase is independently testable)

### Phase 1 — Source view + function code (no orchestrator changes)
1. Pick use case (C: JSON validate-and-tag)
2. Create a source view on usc-tenant: `/usc-tenant/raw-json` (S3 protocol, owner `usc-de-owner`, policy `usc-tenant-s3-policy`)
3. Write the function in `functions/json-validate/` — single `handler.py`, ~30 lines, plus `Dockerfile` and `function.yaml` (vastde-style metadata)
4. Validate the function locally: `docker build`, run with a sample S3 event payload
5. **Acceptance:** image builds, local run prints "valid JSON, tags=[...]" for a fixture

### Phase 2 — Build + push image (uses existing `function build`)
1. Add registry to env (we have `usc-registry` registered from Stage A)
2. `vastde-orch function tag json-validate -c sample/testing/usc-pipeline.yaml` → confirm hash-based tag
3. `vastde-orch function build json-validate -c sample/testing/usc-pipeline.yaml` → build + push
4. **Acceptance:** image visible in the registry with the content-hash tag

### Phase 3 — Pipeline YAML + apply (the orchestrator's first real workout)
1. Write `sample/testing/usc-pipeline.yaml` — minimal: tenant + 1 trigger + 1 function + 1 flow edge. Extends usc-tenant-enable.yaml shape OR is a separate full-schema yaml
2. `vastde-orch validate -c sample/testing/usc-pipeline.yaml` — catch schema issues
3. `vastde-orch apply -c sample/testing/usc-pipeline.yaml --plan` — review plan
4. `vastde-orch apply -c sample/testing/usc-pipeline.yaml` — apply
5. **Acceptance:** trigger appears in `vastde triggers list`, function in `vastde functions list`, pipeline in `vastde pipelines list`, status = `deployed`

### Phase 4 — Real event end-to-end
1. `aws s3 cp test.json s3://raw-json/...` (using usc-de-owner credentials)
2. Watch function logs via `kubectl logs -n vast-dataengine -l function=json-validate` (or whatever the convention is — TBD per phase 3)
3. Verify the S3 object has the new tags
4. **Acceptance:** end-to-end event-to-logs latency < 10s, tags appear on object, no errors in pod logs

### Phase 5 — Idempotency + teardown
1. Re-run `vastde-orch apply` — expect `unchanged` across all resources
2. `vastde-orch destroy -c sample/testing/usc-pipeline.yaml --plan` — review what would go
3. `vastde-orch destroy -c sample/testing/usc-pipeline.yaml --yes` — apply
4. Verify trigger/function/pipeline all gone, source view + bucket retained (those belong to tenant, not pipeline)
5. **Acceptance:** clean re-run shows no resources from this pipeline; tenant-level state untouched

## Decisions to confirm before phase 1

1. **Use case** — recommend C (JSON validate-and-tag). Pick A, B, or C.
2. **Source view** — `/usc-tenant/raw-json` (will be created in phase 1). Different path?
3. **Registry** — assumes `usc-registry` from Stage A (`docker.io` or whatever you set). Confirm reachable from K8s nodes for image pulls.
4. **Function runtime** — `python-3.11` (project default per CLAUDE.md / function template). Stay with that?
5. **What "verify it ran" looks like** — pod logs via kubectl? Or VAST DE Web UI's invocation history? Or both?

## Open risks

- `vastde-orch apply`'s `function build` shells out to `docker` — assumes local docker daemon. Confirmed working on operator Mac?
- The K8s cluster's CSI / Knative / KEDA install (from the zarf playbook) needs to be **healthy** for functions to deploy. If any of those pods are crashlooping, phase 3 will fail with cryptic errors. **Run `kubectl get pods -n vast-dataengine` first** before phase 3.
- Tenant-admin credentials are needed for the underlying `vastde` CLI calls (per `--skip-k8s-bootstrap` learnings from Stage A). Verify `TENANT_ADMIN_PASSWORD` env var maps to usc-tenant's admin.
- The trigger's `object_key_prefix` / `object_key_suffix` filters are S3-style — if the SDK we use for upload doesn't preserve the suffix the trigger expects, no event fires. Test with a fixed filename first.
