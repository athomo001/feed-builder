"""Levanta el Admin API para Playwright E2E, con estado pre-sembrado (un
token security-admin y una entrega en dead-letter). Ver ui/e2e/global-setup.ts.

No usa una instancia OpenCTI real (spec/PROJECT-MAP.md limite conocido): el
Hub arranca standalone, sin conexion a OpenCTI configurada (ver
hub/opencti_settings_store.py); cualquier flujo E2E que dependa de OpenCTI
real (por ejemplo simular con muestra en vivo) debe tolerar el error
esperado ("opencti_not_configured").
"""
import json
import os
import shutil
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(_HERE, ".tmp-e2e-state")
FEEDS_DIR = os.path.join(_HERE, ".tmp-e2e-feeds")
TOKEN_FILE = os.path.join(_HERE, ".auth-token.json")

# Cada corrida arranca de un estado limpio: si no se borra, destinos/politicas
# de una corrida anterior (o de un intento que fallo a mitad de camino) quedan
# pisados y contaminan las aserciones de la siguiente corrida (por ejemplo,
# una politica con dos versiones "draft" en vez de una sola recien creada).
shutil.rmtree(STATE_DIR, ignore_errors=True)
shutil.rmtree(FEEDS_DIR, ignore_errors=True)
os.makedirs(STATE_DIR, exist_ok=True)
os.makedirs(FEEDS_DIR, exist_ok=True)

os.environ["HUB_STATE_DIR"] = STATE_DIR
os.environ["TXT_FEED_DIR"] = FEEDS_DIR
os.environ["ADMIN_UI_ORIGINS"] = "http://localhost:4200"

_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _REPO_ROOT)

from hub.api.token_store import create_token, init_db as init_tokens_db  # noqa: E402
from hub.delivery import DeliveryState  # noqa: E402
from hub.ledger import LedgerEntry, init_db as init_ledger_db, upsert_delivery  # noqa: E402

tokens_conn = init_tokens_db(os.path.join(STATE_DIR, "tokens.sqlite3"))
_, plaintext = create_token(tokens_conn, role="security-admin")

ledger_conn = init_ledger_db(os.path.join(STATE_DIR, "ledger.sqlite3"))
now = datetime.now(timezone.utc)
upsert_delivery(
    ledger_conn,
    LedgerEntry(
        event_id="e2e-seed-event",
        stix_id="indicator--e2e-seed",
        destination_id="e2e-seed-dest",
        policy_version=1,
        state=DeliveryState.DEAD_LETTER,
        created_at=now,
        updated_at=now,
        error="seeded for e2e",
        attempts=8,
    ),
)

with open(TOKEN_FILE, "w", encoding="utf-8") as f:
    json.dump({"token": plaintext}, f)

print("E2E_SEED_READY", flush=True)

from hub.api.app import create_app  # noqa: E402
from hub.config import load_config  # noqa: E402

import uvicorn  # noqa: E402

app = create_app(load_config())
uvicorn.run(app, host="127.0.0.1", port=8000)
