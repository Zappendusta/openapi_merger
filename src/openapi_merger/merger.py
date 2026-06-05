from __future__ import annotations
import copy

import structlog

log = structlog.get_logger()

# Type alias for clarity
Source = tuple[str, str, dict]  # (name, schema_prefix, doc)


def rewrite_ref(node, old_name: str, new_name: str):
    """Recursively rewrite references to a renamed schema.

    Handles both `$ref` strings and `discriminator.mapping` values. The mapping
    entries may be full refs (`#/components/schemas/Foo`) or bare schema names
    (`Foo`); both forms are rewritten.
    """
    old_ref = f"#/components/schemas/{old_name}"
    new_ref = f"#/components/schemas/{new_name}"
    if isinstance(node, dict):
        result = {}
        for k, v in node.items():
            if k == "$ref" and v == old_ref:
                result[k] = new_ref
            elif k == "discriminator" and isinstance(v, dict):
                result[k] = _rewrite_discriminator(v, old_name, new_name, old_ref, new_ref)
            else:
                result[k] = rewrite_ref(v, old_name, new_name)
        return result
    if isinstance(node, list):
        return [rewrite_ref(item, old_name, new_name) for item in node]
    return node


def _rewrite_discriminator(disc: dict, old_name: str, new_name: str, old_ref: str, new_ref: str) -> dict:
    out = dict(disc)
    mapping = disc.get("mapping")
    if isinstance(mapping, dict):
        out["mapping"] = {
            k: (new_ref if v == old_ref else new_name if v == old_name else v)
            for k, v in mapping.items()
        }
    return out


HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def assign_unique_operation_ids(sources: list[Source]) -> list[dict]:
    """
    Walk all operations across all sources in order and ensure every operationId
    is globally unique. Mutates source docs in place.

    Resolution strategy per conflict:
      1. Try the source's prefix: `{prefix}{op_id}`
      2. Still taken (or prefix is empty): append `_2`, `_3`, ... up to `_1000`.

    Returns a list of rename records: {source, path, method, old, new, reason}.
    The `reason` field is one of:
      - "post_prefix" — the source prefix was non-empty and also collided, so a numeric suffix was used.
      - "within_source" — the duplicate originated in the same source, and was resolved by the source prefix.
      - "cross_source" — the duplicate originated in a different source, and was resolved by the source prefix.
    `post_prefix` takes precedence over the other two when the prefix attempt itself failed.
    """
    seen: set[str] = set()
    origin: dict[str, str] = {}
    renames: list[dict] = []

    for source_name, prefix, doc in sources:
        for path, path_item in doc.get("paths", {}).items():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if method not in HTTP_METHODS:
                    continue
                if not isinstance(operation, dict):
                    continue
                op_id = operation.get("operationId")
                if not op_id:
                    continue

                if op_id not in seen:
                    seen.add(op_id)
                    origin[op_id] = source_name
                    continue

                reason = "within_source" if origin[op_id] == source_name else "cross_source"

                candidate: str | None = None
                prefixed = f"{prefix}{op_id}" if prefix else None
                if prefixed and prefixed not in seen:
                    candidate = prefixed
                else:
                    if prefixed is not None:
                        reason = "post_prefix"
                    for n in range(2, 1001):
                        suffixed = f"{op_id}_{n}"
                        if suffixed not in seen:
                            candidate = suffixed
                            break
                    if candidate is None:
                        raise RuntimeError(
                            f"could not assign unique operationId for '{op_id}' in source '{source_name}': "
                            f"candidates '{op_id}_2'..'{op_id}_1000' all taken"
                        )

                operation["operationId"] = candidate
                seen.add(candidate)
                origin[candidate] = source_name
                renames.append({
                    "source": source_name,
                    "path": path,
                    "method": method,
                    "old": op_id,
                    "new": candidate,
                    "reason": reason,
                })

    return renames


def detect_schema_collisions(sources: list[Source]) -> dict[str, list[str]]:
    """
    Returns schema_name -> [source_names] for names that appear in multiple
    sources with different content. Equal-content duplicates are not collisions.
    """
    schema_map: dict[str, list[tuple[str, dict]]] = {}
    for source_name, _prefix, doc in sources:
        for name, schema in doc.get("components", {}).get("schemas", {}).items():
            schema_map.setdefault(name, []).append((source_name, schema))

    collisions = {}
    for name, entries in schema_map.items():
        if len(entries) <= 1:
            continue
        first = entries[0][1]
        if all(e[1] == first for e in entries[1:]):
            continue  # all equal: not a collision
        collisions[name] = [e[0] for e in entries]
    return collisions


def merge_specs(sources: list[Source], title: str, version: str) -> dict:
    collisions = detect_schema_collisions(sources)

    for name, sources_with_name in collisions.items():
        log.warning(
            "merge.collision.schema",
            name=name,
            sources=sources_with_name,
            resolution="prefix",
        )

    # Apply schema prefix and $ref rewrites per source.
    processed: list[Source] = []
    for source_name, prefix, doc in sources:
        doc = copy.deepcopy(doc)
        colliding_schemas = [
            name for name, names in collisions.items() if source_name in names
        ]
        for name in colliding_schemas:
            new_name = f"{prefix}{name}"
            schemas = doc.setdefault("components", {}).setdefault("schemas", {})
            if name in schemas:
                schemas[new_name] = schemas.pop(name)
            doc = rewrite_ref(doc, name, new_name)
        processed.append((source_name, prefix, doc))

    # Resolve operationIds globally across the full processed set, mutating in place.
    renames = assign_unique_operation_ids(processed)
    for r in renames:
        log.warning(
            "merge.collision.operation_id",
            operation_id=r["old"],
            new_operation_id=r["new"],
            source=r["source"],
            path=r["path"],
            method=r["method"],
            reason=r["reason"],
            resolution="prefix" if r["reason"] in {"within_source", "cross_source"} else "numeric_suffix",
        )

    # Merge paths — error on duplicates
    merged_paths: dict = {}
    for source_name, _prefix, doc in processed:
        for path, value in doc.get("paths", {}).items():
            if path in merged_paths:
                log.error(
                    "merge.path_collision",
                    path=path,
                    source=source_name,
                )
                raise RuntimeError(
                    f"Path collision: '{path}' found in '{source_name}' and an earlier source"
                )
            merged_paths[path] = value

    # Merge schemas — equal duplicates are silently deduped
    merged_schemas: dict = {}
    for _source_name, _prefix, doc in processed:
        for name, schema in doc.get("components", {}).get("schemas", {}).items():
            if name not in merged_schemas:
                merged_schemas[name] = schema

    # Merge other component sub-objects
    other_component_keys = {
        "responses", "parameters", "requestBodies",
        "headers", "examples", "links", "callbacks",
    }
    merged_components: dict = {"schemas": merged_schemas}
    for _source_name, _prefix, doc in processed:
        for key in other_component_keys:
            items = doc.get("components", {}).get(key, {})
            if items:
                merged_components.setdefault(key, {}).update(items)

    openapi_version = next(
        (doc.get("openapi", "3.0.0") for _, _, doc in processed), "3.0.0"
    )

    return {
        "openapi": openapi_version,
        "info": {"title": title, "version": version},
        "paths": merged_paths,
        "components": merged_components,
    }
