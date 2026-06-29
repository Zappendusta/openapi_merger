# Per-route origin marking

**Date:** 2026-06-29
**Status:** Approved

## Goal

Mark every merged route with the upstream API it originated from, so that:

1. **Doc grouping** — the (self-written) renderer can group routes into sections by source API.
2. **Filtering** — downstream consumers can select/route operations by origin.

## Decision

Stamp a vendor extension `x-origin-api: "<source name>"` onto every *operation*
in each source's spec, during the transform step.

- **Operation-level**, not path-level: it is the unit filtering consumers
  iterate, and operation-level `x-*` extensions are the most reliably preserved
  across the external merge CLIs (redocly / speakeasy / openapi-merge).
- **Value = source `name` verbatim** (e.g. `"absence api"`). No config change —
  `name` already exists on `SourceConfig`. A dedicated machine-key field is
  YAGNI for now.
- **`tags` untouched.** Origin lives entirely in the custom field, so upstream
  tag semantics survive and there is no SDK-namespace / nav-duplication
  entanglement.

### Rejected alternatives

- **Path-item-level extension** — DRYer, but marginally less reliably preserved
  by external CLIs, and forces filtering consumers to do a parent lookup.
- **Append origin to `tags`** — if upstream specs already carry tags, operations
  get listed under both their real tag and the origin tag (duplicated nav).
- **`x-tagGroups`** — Redoc/Stoplight-specific; unnecessary given full control
  of the renderer.

## Implementation

### `transformer.py`

`transform_paths` gains an `origin: str` parameter. After computing `new_path`,
walk the path-item value and stamp `x-origin-api` onto each HTTP-method
operation.

- Operations are the standard method keys only:
  `get, put, post, delete, options, head, patch, trace`.
- Other path-item keys (`parameters`, `summary`, `description`, `servers`,
  `$ref`, path-level `x-*`) are left alone.
- If an operation already has `x-origin-api`, overwrite it — this service is the
  authority on origin.
- Defensively skip a path-item or operation value that is not a dict rather than
  crashing the merge.

### `orchestrator.py`

The per-source transform call (`orchestrator.py:54`) passes `source.name` as
`origin`. The marking happens *before* `self._strategy.merge(...)`, so all four
engines receive identically-marked input — no per-engine work.

## Error handling

No new failure modes — pure dict mutation on already-fetched docs. Malformed
(non-dict) path-item or operation values are skipped defensively.

## Testing

- **Unit** (`tests/test_transformer.py`): operations receive the field;
  non-operation path-item keys are untouched; value matches the passed origin;
  interplay with existing route-transform and `discard_paths` logic.
- **Preservation** (`tests/mergers/` + `tests/e2e/`): assert `x-origin-api`
  survives each *available* merge engine's round-trip, catching any external CLI
  that strips operation-level extensions. External-CLI tests skip when the
  binary is absent (existing pattern).
