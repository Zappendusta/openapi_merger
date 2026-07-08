import os

import uvicorn

from openapi_merger.config import load_service_config


def main() -> None:
    cfg = load_service_config(os.getenv("SERVICE_CONFIG", "/config/service.yaml"))
    port = int(os.getenv("PORT", cfg.port))
    uvicorn.run("openapi_merger.main:app", host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
