from openapi_merger.config import RouteTransform


def transform_paths(paths: dict, transforms: list[RouteTransform]) -> dict:
    result = {}
    for path, value in paths.items():
        new_path = path
        for t in transforms:
            if new_path.startswith(t.from_path):
                new_path = t.to + new_path[len(t.from_path):]
        result[new_path] = value
    return result
