"""`python -m hub.api` levanta el Admin API con uvicorn. Este entrypoint
esta separado de `hub.api.app` para que importar `create_app` en tests no
dispare `load_config()` (que exige OPENCTI_URL/OPENCTI_SERVICE_ACCOUNT_TOKEN)
ni levante un servidor real.

Autor: Athan Espinoza
"""
import os

import uvicorn

from hub.api.app import create_app
from hub.config import load_config


def main() -> None:
    config = load_config()
    app = create_app(config)
    uvicorn.run(app, host=os.environ.get("HUB_API_HOST", "0.0.0.0"), port=int(os.environ.get("HUB_API_PORT", "8000")))


if __name__ == "__main__":
    main()
