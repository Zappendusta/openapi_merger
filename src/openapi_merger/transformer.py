from openapi_merger.config import RouteTransform

OPERATION_METHODS = frozenset(
    {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
)


def transform_paths(
    paths: dict,
    transforms: list[RouteTransform],
    discard_paths: list[str] = [],
    origin: str | None = None,
) -> dict:
    result = {}
    for path, value in paths.items():
        if any(path == prefix or path.startswith(prefix.rstrip("/") + "/") for prefix in discard_paths):
            continue
        new_path = path
        for t in transforms:
            if new_path.startswith(t.from_path):
                new_path = t.to + new_path[len(t.from_path):]
        if origin is not None and isinstance(value, dict):
            for method, operation in value.items():
                if method.lower() in OPERATION_METHODS and isinstance(operation, dict):
                    operation["x-origin-api"] = origin
        result[new_path] = value
    return result
