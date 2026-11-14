import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


class ConfigurationStore:
    """Gestiona materiales y secciones con copias históricas atómicas."""

    def __init__(self, config_root: Path, history_root: Path):
        self.config_root = config_root
        self.history_root = history_root

    def materials(self) -> List[Dict[str, Any]]:
        return self._read("materials.json").get("materials", [])

    def save_material(self, material: Dict[str, Any], previous_name: str = None) -> None:
        data = self._read("materials.json")
        materials = data.setdefault("materials", [])
        key = previous_name or material["name"]
        index = next((i for i, item in enumerate(materials) if item["name"] == key), None)
        if index is None:
            materials.append(material)
        else:
            materials[index] = material
        self._write("materials.json", data)

    def delete_material(self, name: str) -> None:
        data = self._read("materials.json")
        materials = data.get("materials", [])
        filtered = [item for item in materials if item["name"] != name]
        if len(filtered) == len(materials):
            raise KeyError("Material no encontrado")
        data["materials"] = filtered
        self._write("materials.json", data)

    def work_sections(self) -> Dict[str, List[float]]:
        return self._read("work_sections.json").get("positions", {})

    def save_work_section(self, name: str, position: List[float], previous_name: str = None) -> None:
        data = self._read("work_sections.json")
        positions = data.setdefault("positions", {})
        if previous_name and previous_name != name:
            positions.pop(previous_name, None)
        positions[name] = position
        self._write("work_sections.json", data)

    def delete_work_section(self, name: str) -> None:
        data = self._read("work_sections.json")
        positions = data.get("positions", {})
        if name not in positions:
            raise KeyError("Sección de trabajo no encontrada")
        del positions[name]
        self._write("work_sections.json", data)

    def simulation_parameters(self) -> Dict[str, Any]:
        return self._read("simulation_parameters.json")

    def save_simulation_parameters(self, parameters: Dict[str, Any]) -> None:
        self._write("simulation_parameters.json", parameters)

    def simulation_profiles(self, filename="simulation_profiles.json") -> List[Dict[str, Any]]:
        if not (self.config_root / filename).is_file():
            return []
        return self._read(filename).get("profiles", [])

    def save_simulation_profile(
        self,
        name: str,
        parameters: Dict[str, Any],
        filename="simulation_profiles.json",
    ) -> Dict[str, Any]:
        profiles = self.simulation_profiles(filename)
        normalized_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
        profile_id = re.sub(r"[^a-z0-9]+", "-", normalized_name.lower()).strip("-") or "configuracion"
        profile = {
            "profile_id": profile_id,
            "name": name,
            "parameters": parameters,
        }
        index = next(
            (i for i, item in enumerate(profiles) if item["profile_id"] == profile_id),
            None,
        )
        if index is None:
            profiles.append(profile)
        else:
            profiles[index] = profile
        self._write(filename, {"profiles": profiles})
        return profile

    def delete_simulation_profile(self, profile_id: str, filename="simulation_profiles.json") -> None:
        profiles = self.simulation_profiles(filename)
        filtered = [
            profile for profile in profiles
            if profile.get("profile_id") != profile_id
        ]
        if len(filtered) == len(profiles):
            raise KeyError("Configuración guardada no encontrada")
        self._write(filename, {"profiles": filtered})

    def _read(self, filename: str) -> Dict[str, Any]:
        with (self.config_root / filename).open("r", encoding="utf-8") as stream:
            return json.load(stream)

    def _write(self, filename: str, data: Dict[str, Any]) -> None:
        source = self.config_root / filename
        history = self.history_root / source.stem
        history.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        if source.is_file():
            with (history / (source.stem + "-" + stamp + ".json")).open("w", encoding="utf-8") as stream:
                json.dump(self._read(filename), stream, indent=2, ensure_ascii=False)
        temporary = source.with_suffix(source.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
        temporary.replace(source)
