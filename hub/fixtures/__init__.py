"""Loader de fixtures STIX anonimizadas usadas por los tests de Entrega 0."""
import json
from pathlib import Path

STIX_FIXTURES_DIR = Path(__file__).parent / "stix"


def load_stix_fixture(name: str) -> dict:
    path = STIX_FIXTURES_DIR / f"{name}.json"
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)
