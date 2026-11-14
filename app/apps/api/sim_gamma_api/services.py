"""Independent service graph for each supported irradiator workflow."""
from dataclasses import dataclass
from threading import RLock

from sim_gamma.domain.irradiators import current_irradiator, definition
from sim_gamma.domain.route_dose import WORKFLOW, SOURCE_DIAMETER_MM
from sim_gamma.application.services.case_store import CaseStore
from sim_gamma.application.services.configuration_store import ConfigurationStore
from sim_gamma.application.services.source_inventory import SourceInventory
from sim_gamma.application.services.cad_preview import CadPreviewService
from sim_gamma.application.services.deterministic_jobs import DeterministicJobs
from sim_gamma.application.services.monte_carlo_jobs import MonteCarloJobs
from .settings import settings


@dataclass
class IrradiatorServices:
    case_store: CaseStore
    configuration_store: ConfigurationStore
    source_inventory: SourceInventory
    cad_preview: CadPreviewService
    deterministic_jobs: DeterministicJobs
    monte_carlo_jobs: MonteCarloJobs


def build_panoramic_wet(category: str) -> IrradiatorServices:
    history = settings.output_root / "config_history"
    diameter = SOURCE_DIAMETER_MM if definition(category).get("transport") == WORKFLOW else None
    cases = CaseStore(settings.output_root, settings.config_root, settings.example_step,
                      settings.deterministic_stl, settings.cases_directory_name,
                      irradiator_type=category)
    return IrradiatorServices(
        cases,
        ConfigurationStore(settings.config_root, history),
        SourceInventory(settings.config_root / "sources.json", history / "sources", diameter_mm=diameter),
        CadPreviewService(settings.cache_root / "step_previews"),
        DeterministicJobs(cases, settings.project_root),
        MonteCarloJobs(cases, settings.project_root, settings.monte_carlo_execution_enabled),
    )


# A future category must supply its own workflow factory before being enabled.
WORKFLOW_FACTORIES = {"panoramic_wet": build_panoramic_wet}
_instances: dict[str, IrradiatorServices] = {}
_lock = RLock()


def services_for_current_category() -> IrradiatorServices:
    category = current_irradiator.get()
    item = definition(category, require_enabled=True)
    with _lock:
        if category not in _instances:
            factory = WORKFLOW_FACTORIES.get(item["workflow"])
            if factory is None:
                raise ValueError(f"No se implementó el flujo de la categoría {category}")
            _instances[category] = factory(category)
        return _instances[category]


class ScopedService:
    """Resolve legacy route helpers against the category of this HTTP request."""
    def __init__(self, name: str):
        self.name = name

    def __getattr__(self, attribute):
        return getattr(getattr(services_for_current_category(), self.name), attribute)


def stop_all_jobs() -> None:
    for services in list(_instances.values()):
        services.deterministic_jobs.cancel_all()
        services.monte_carlo_jobs.cancel_all()
