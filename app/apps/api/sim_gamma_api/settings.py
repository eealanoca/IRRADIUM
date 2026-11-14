from dataclasses import dataclass
import os
from pathlib import Path
from sim_gamma.domain.irradiators import current_irradiator


@dataclass(frozen=True)
class Settings:
    project_root: Path = Path(__file__).resolve().parents[3]
    monte_carlo_execution_enabled: bool = os.environ.get(
        "SIM_GAMMA_ENABLE_MONTE_CARLO", "1"
    ).strip().lower() not in {"0", "false", "no", "off"}
    # Temporarily disabled for the fixed-budget workflow. Historical uncertainty
    # targets remain in archived studies and must never be rewritten as achieved.
    statistical_refinement_enabled: bool = os.environ.get(
        "IRRADIUM_ENABLE_STATISTICAL_REFINEMENT", "0"
    ).strip().lower() in {"1", "true", "yes", "on"}

    @property
    def workspace_root(self) -> Path:
        configured = os.environ.get("IRRADIUM_WORKSPACE_ROOT") or os.environ.get("SIM_GAMMA_WORKSPACE_ROOT")
        return Path(configured).expanduser().resolve() if configured else self.project_root.parent / "workspace"

    @property
    def output_root(self) -> Path:
        return self.workspace_root / "irradiators" / current_irradiator.get()

    @property
    def config_root(self) -> Path:
        return self.output_root / "Configuracion"

    @property
    def cases_directory_name(self) -> str:
        configured = os.environ.get("SIM_GAMMA_CASES_DIRECTORY", "Casos").strip()
        if not configured or configured in {".", ".."} or "/" in configured:
            raise ValueError("SIM_GAMMA_CASES_DIRECTORY debe ser un nombre de carpeta")
        return configured

    @property
    def cache_root(self) -> Path:
        return self.output_root / "Temporales"

    @property
    def movement_output(self) -> Path:
        return self.output_root / "Salidas/Trayectorias/movements_parameters_optimized.json"

    @property
    def example_step(self) -> Path:
        configured = os.environ.get("SIM_GAMMA_EXAMPLE_STEP")
        return Path(configured).expanduser().resolve() if configured else self.project_root / "data" / "examples" / "load.step"

    @property
    def deterministic_stl(self) -> Path:
        configured = os.environ.get("SIM_GAMMA_DETERMINISTIC_STL")
        return Path(configured).expanduser().resolve() if configured else self.project_root / "legacy" / "assets" / "world_load.stl"

    @property
    def source_root(self) -> Path:
        return self.project_root / "src"


settings = Settings()
