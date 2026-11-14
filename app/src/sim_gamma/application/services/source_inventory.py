import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class SourceInventory:
    """Lee el inventario existente y calcula su actividad a una fecha dada."""

    def __init__(self, source_file: Path, history_root: Path = None, *, diameter_mm: float | None = None):
        self.source_file = source_file
        self.history_root = history_root
        self.diameter_mm = diameter_mm

    def _load(self) -> Dict[str, Any]:
        with self.source_file.open("r", encoding="utf-8") as stream:
            return json.load(stream)

    def radionuclides(self) -> Dict[str, Any]:
        return self._load().get("radionuclides", {})

    def add(self, source: Dict[str, Any]) -> None:
        data = self._load()
        self._validate_identifier(data.get("sources", []), source.get("identifier"))
        data.setdefault("sources", []).append(source)
        self._write_versioned(data)

    def update(self, index: int, source: Dict[str, Any]) -> None:
        data = self._load()
        sources = data.get("sources", [])
        if index < 0 or index >= len(sources):
            raise IndexError("Fuente no encontrada")
        self._validate_identifier(sources, source.get("identifier"), excluded_index=index)
        sources[index] = source
        self._write_versioned(data)

    @staticmethod
    def _validate_identifier(
        sources: List[Dict[str, Any]],
        identifier: Optional[str],
        excluded_index: Optional[int] = None,
    ) -> None:
        normalized = str(identifier or "").strip().casefold()
        if not normalized:
            raise ValueError("El identificador de la fuente es obligatorio")
        for index, configured in enumerate(sources):
            if index == excluded_index:
                continue
            configured_identifier = str(
                configured.get("identifier") or "SRC-%03d" % (index + 1)
            ).strip().casefold()
            if configured_identifier == normalized:
                raise ValueError("Ya existe una fuente con ese identificador")

    def delete(self, index: int) -> None:
        data = self._load()
        sources = data.get("sources", [])
        if index < 0 or index >= len(sources):
            raise IndexError("Fuente no encontrada")
        sources.pop(index)
        self._write_versioned(data)

    def _write_versioned(self, data: Dict[str, Any]) -> None:
        if self.history_root is not None:
            self.history_root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = self.history_root / ("sources-%s.json" % stamp)
            with backup.open("w", encoding="utf-8") as stream:
                json.dump(self._load(), stream, indent=4, ensure_ascii=False)

        temporary = self.source_file.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=4, ensure_ascii=False)
        temporary.replace(self.source_file)

    def sources(
        self,
        reference_date: Optional[date] = None,
        radionuclide: str = "Co-60",
    ) -> Dict[str, Any]:
        data = self._load()
        raw_sources = data.get("sources", [])
        nuclide = data.get("radionuclides", {}).get(radionuclide)
        if nuclide is None:
            raise ValueError("Radionúclido no configurado: %s" % radionuclide)

        half_life_days = float(nuclide["half_life"])
        selected_date = reference_date or date.today()
        enriched: List[Dict[str, Any]] = []

        for index, source in enumerate(raw_sources, start=1):
            reading_date = datetime.strptime(
                source["initial_reading_date"], "%Y-%m-%d"
            ).date()
            elapsed_days = max(0, (selected_date - reading_date).days)
            initial_activity = float(source["intensity"])
            decay_constant = math.log(2.0) / half_life_days
            current_activity = initial_activity * math.exp(
                -decay_constant * elapsed_days
            )
            lower = [float(value) for value in source["lower_point"]]
            upper = [float(value) for value in source["upper_point"]]

            enriched.append(
                {
                    "id": str(source.get("identifier") or "SRC-%03d" % index),
                    "index": index - 1,
                    "radionuclide": radionuclide,
                    "initial_activity_bq": initial_activity,
                    "current_activity_bq": current_activity,
                    "initial_activity_kci": initial_activity / 3.7e13,
                    "current_activity_kci": current_activity / 3.7e13,
                    "remaining_fraction": current_activity / initial_activity,
                    "initial_reading_date": reading_date.isoformat(),
                    "lower_point_mm": lower,
                    "upper_point_mm": upper,
                    "center_point_mm": [
                        (lower[axis] + upper[axis]) / 2.0 for axis in range(3)
                    ],
                    "active_length_mm": math.dist(lower, upper),
                    **({"diameter_mm": self.diameter_mm} if self.diameter_mm is not None else {}),
                    "status": "configured",
                }
            )

        return {
            "reference_date": selected_date.isoformat(),
            "radionuclide": radionuclide,
            "half_life_days": half_life_days,
            "count": len(enriched),
            "total_initial_activity_bq": sum(
                item["initial_activity_bq"] for item in enriched
            ),
            "total_current_activity_bq": sum(
                item["current_activity_bq"] for item in enriched
            ),
            "total_initial_activity_kci": sum(
                item["initial_activity_kci"] for item in enriched
            ),
            "total_current_activity_kci": sum(
                item["current_activity_kci"] for item in enriched
            ),
            "sources": enriched,
        }
