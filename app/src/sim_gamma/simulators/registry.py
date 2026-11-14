from typing import Dict

from .contracts import MonteCarloBackend
from .openmc.backend import OpenMCBackend


class BackendRegistry:
    def __init__(self, monte_carlo_enabled: bool = False):
        self._backends: Dict[str, MonteCarloBackend] = {}
        self.register(OpenMCBackend(execution_enabled=monte_carlo_enabled))

    def register(self, backend: MonteCarloBackend) -> None:
        self._backends[backend.backend_id] = backend

    def get(self, backend_id: str) -> MonteCarloBackend:
        try:
            return self._backends[backend_id]
        except KeyError as error:
            raise ValueError("Backend Monte Carlo desconocido: %s" % backend_id) from error

    def capabilities(self):
        return [
            {
                "id": backend.backend_id,
                "execution_enabled": bool(getattr(backend, "execution_enabled", False)),
            }
            for backend in self._backends.values()
        ]
