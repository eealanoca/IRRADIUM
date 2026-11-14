from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


@dataclass(frozen=True)
class MonteCarloRequest:
    case_directory: Path
    movement_parameters: Path
    geometry_step: Path
    particles: int
    cell_size_mm: float
    cpu_cores: int = -1


class MonteCarloBackend(ABC):
    backend_id: str

    @abstractmethod
    def prepare(self, request: MonteCarloRequest) -> Dict[str, Any]:
        """Valida y prepara archivos sin ejecutar el simulador."""

    @abstractmethod
    def execute(self, request: MonteCarloRequest) -> Dict[str, Any]:
        """Ejecuta el simulador cuando la política de despliegue lo permite."""
