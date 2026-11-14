from pathlib import Path
from typing import Any, Dict

from ..contracts import MonteCarloBackend, MonteCarloRequest


class OpenMCBackend(MonteCarloBackend):
    """Adaptador registrable del flujo OpenMC con geometría DAGMC."""

    backend_id = "openmc-0.15.3-dagmc"

    def __init__(self, execution_enabled: bool = False):
        self.execution_enabled = execution_enabled

    def prepare(self, request: MonteCarloRequest) -> Dict[str, Any]:
        required = {
            "movement_parameters": request.movement_parameters,
            "geometry_step": request.geometry_step,
        }
        missing = [name for name, path in required.items() if not Path(path).is_file()]
        if missing:
            raise FileNotFoundError("Entradas Monte Carlo faltantes: %s" % ", ".join(missing))

        return {
            "backend": self.backend_id,
            "state": "prepared",
            "execution_enabled": self.execution_enabled,
            "particles": request.particles,
            "cell_size_mm": request.cell_size_mm,
            "cpu_cores": request.cpu_cores,
        }

    def execute(self, request: MonteCarloRequest) -> Dict[str, Any]:
        if not self.execution_enabled:
            raise RuntimeError(
                "La ejecución Monte Carlo está deshabilitada por configuración. "
                "La preparación y el diseño permanecen disponibles."
            )
        raise NotImplementedError(
            "La ejecución se conectará al flujo OpenMC existente después de su validación explícita."
        )
