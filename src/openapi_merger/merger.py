from __future__ import annotations
import copy

# Type alias for clarity
Source = tuple[str, str, dict]  # (name, schema_prefix, doc)


def rewrite_ref(node, old_name: str, new_name: str):
    """Recursively rewrite $ref values for a specific schema name."""
    old_ref = f"#/components/schemas/{old_name}"
    new_ref = f"#/components/schemas/{new_name}"
    if isinstance(node, dict):
        return {
            k: (new_ref if k == "$ref" and v == old_ref else rewrite_ref(v, old_name, new_name))
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [rewrite_ref(item, old_name, new_name) for item in node]
    return node


HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def detect_operation_id_collisions(sources: list[Source]) -> dict[str, list[str]]:
    """
    Returns operationId -> [source_names] for IDs that appear in multiple
    sources with different operation content. Equal-content duplicates are not
    collisions (mirrors detect_schema_collisions behaviour).
    """
    op_map: dict[str, list[tuple[str, dict]]] = {}
    for source_name, _prefix, doc in sources:
        for _path, path_item in doc.get("paths", {}).items():
            for method, operation in path_item.items():
                if method not in HTTP_METHODS:
                    continue
                if not isinstance(operation, dict):
                    continue
                op_id = operation.get("operationId")
                if op_id:
                    op_map.setdefault(op_id, []).append((source_name, operation))

    collisions: dict[str, list[str]] = {}
    for op_id, entries in op_map.items():
        if len(entries) <= 1:
            continue
        first = entries[0][1]
        if all(e[1] == first for e in entries[1:]):
            continue  # all equal: not a collision
        collisions[op_id] = [e[0] for e in entries]
    return collisions


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

    # Apply prefix only to colliding schema names (and their $refs) per source
    processed: list[Source] = []
    for source_name, prefix, doc in sources:
        doc = copy.deepcopy(doc)
        colliding = [
            name for name, names in collisions.items() if source_name in names
        ]
        for name in colliding:
            new_name = f"{prefix}{name}"
            schemas = doc.setdefault("components", {}).setdefault("schemas", {})
            if name in schemas:
                schemas[new_name] = schemas.pop(name)
            doc = rewrite_ref(doc, name, new_name)
        processed.append((source_name, prefix, doc))

    # Merge paths — error on duplicates
    merged_paths: dict = {}
    for source_name, _prefix, doc in processed:
        for path, value in doc.get("paths", {}).items():
            if path in merged_paths:
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
