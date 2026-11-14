"""Irradiator catalog and request-local category selection."""
from contextvars import ContextVar
from functools import lru_cache
import json
from pathlib import Path

DEFAULT_IRRADIATOR = "IV"
current_irradiator: ContextVar[str] = ContextVar("irradium_irradiator", default=DEFAULT_IRRADIATOR)


class UnsupportedIrradiator(ValueError):
    pass


@lru_cache(maxsize=1)
def catalog() -> tuple[dict, ...]:
    path = Path(__file__).resolve().parents[3] / "data/irradiator_types.json"
    return tuple(json.loads(path.read_text(encoding="utf-8")))


def definition(category: str, *, require_enabled: bool = False) -> dict:
    item = next((item for item in catalog() if item["id"] == category), None)
    if item is None:
        raise ValueError(f"Categoría de irradiador desconocida: {category}")
    if require_enabled and not item["enabled"]:
        raise UnsupportedIrradiator(f"La categoría {category} todavía no está habilitada en IRRADIUM")
    return item
