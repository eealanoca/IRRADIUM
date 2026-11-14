"""Inputs for the suspended load workflow."""
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field, FiniteFloat, field_validator, model_validator

from sim_gamma.domain.route_study import ACCUMULATED_ERROR_LIMIT, DEFAULT_STUDY_HISTORIES, EXTENDED_STUDY_SECONDS, STANDARD_STUDY_SECONDS


class RoutePosition(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    center_mm: tuple[FiniteFloat, FiniteFloat, FiniteFloat]
    yaw_degrees: FiniteFloat = Field(default=0, ge=-360, le=360)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value):
        if not value.strip():
            raise ValueError("Cada posición necesita un nombre")
        return value.strip()


class RouteConfiguration(BaseModel):
    positions: list[RoutePosition] = Field(default_factory=list, max_length=100)
    calculation_positions: list[str] | None = Field(default=None, min_length=1, max_length=100)
    radionuclide: str = Field(default="Co-60", min_length=1, max_length=40)
    cpu_cores: int = Field(default=-1, ge=-1)
    particles: int = Field(default=DEFAULT_STUDY_HISTORIES, ge=2, le=10**12)
    batches: int = Field(default=20, ge=2, le=10000)
    cell_size_cm: FiniteFloat = Field(default=2, gt=0, le=100)
    relative_error_target: FiniteFloat = Field(default=.05, gt=0, le=1)
    accumulated_error_target: FiniteFloat = Field(default=ACCUMULATED_ERROR_LIMIT, gt=0, le=ACCUMULATED_ERROR_LIMIT)
    interval_max_particles: int = Field(default=300_000_000, ge=2, le=10**12)
    interval_time_limit_seconds: int = Field(default=18000, ge=60, le=86400)
    dose_tally: Literal["flux", "heating", "alanine", "phits"] = "heating"
    occupancy: Literal["isolated", "occupied"] = "isolated"
    position_reference: Literal["center", "bottom_center"] = "center"
    variance_reduction: Literal["analog", "magic", "source_mixture", "source_box", "source_box_windows", "source_box_magic", "source_scene", "phits_importance", "phits_windows"] = "analog"
    source_mixture_fraction: FiniteFloat = Field(default=.5, gt=0, lt=1)
    cad_offset_degrees: FiniteFloat = Field(default=0, ge=-360, le=360)
    deterministic_samples: int = Field(default=10000, ge=100, le=1000000)

    @field_validator("positions")
    @classmethod
    def unique_names(cls, positions):
        if len({position.name.casefold() for position in positions}) != len(positions):
            raise ValueError("Los nombres de posición no se pueden repetir")
        return positions

    @model_validator(mode="after")
    def validate_calculation_positions(self):
        if self.calculation_positions is not None:
            from sim_gamma.domain.route_study import selected_position_indices
            selected_position_indices(
                [position.model_dump() for position in self.positions],
                {"position_names": self.calculation_positions},
            )
        return self


class RouteStudyRequest(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    position_names: list[str] | None = Field(default=None, min_length=1, max_length=100)
    irradiation_date: date | None = None
    quick: bool = False
    adaptive_statistics: bool = False
    accumulated_error_target: FiniteFloat | None = Field(default=None, gt=0, le=.02)
    budget_reference_run_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    time_limit_seconds: int = Field(default=STANDARD_STUDY_SECONDS, ge=1, le=EXTENDED_STUDY_SECONDS)
    extended_time: bool = False
    memory_limit_mb: int = Field(default=4096, ge=512, le=8192)
    occupancy: Literal["isolated", "occupied"] | None = None
    cpu_cores: int | None = Field(default=None, ge=1, le=64)
    particles: int | None = Field(default=None, ge=2, le=10**12)
    batches: int | None = Field(default=None, ge=2, le=10000)
    cell_size_cm: FiniteFloat | None = Field(default=None, gt=0, le=100)
    relative_error_target: FiniteFloat | None = Field(default=None, gt=0, le=.10)
    variance_reduction: Literal["analog", "magic", "source_mixture", "source_box", "source_box_windows", "source_box_magic", "source_scene", "phits_importance", "phits_windows"] | None = None
    source_mixture_fraction: FiniteFloat | None = Field(default=None, gt=0, lt=1)

    @model_validator(mode="after")
    def require_extended_time(self):
        if self.accumulated_error_target is not None and (self.quick or self.adaptive_statistics):
            raise ValueError("El objetivo acumulado requiere el modo por intervalo y una ejecución completa.")
        if self.budget_reference_run_id is not None and self.accumulated_error_target is None:
            raise ValueError("La guía de historias requiere un objetivo acumulado.")
        if self.time_limit_seconds > STANDARD_STUDY_SECONDS and (not self.extended_time or self.quick):
            raise ValueError("Activa el presupuesto extendido para superar los 15 minutos en un estudio completo")
        return self


class RouteResumeRequest(BaseModel):
    run_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")


class AirReferenceRequest(BaseModel):
    point_mm: tuple[FiniteFloat, FiniteFloat, FiniteFloat]
    irradiation_date: date | None = None

    @field_validator("point_mm")
    @classmethod
    def validate_point(cls, point):
        if any(abs(value) > 1_000_000 for value in point):
            raise ValueError("Introduce coordenadas dentro de ±1 000 000 mm.")
        return point


AirLoadLength = Annotated[FiniteFloat, Field(ge=1, le=5000)]


class AirRouteCalibrationRequest(BaseModel):
    dimensions_mm: tuple[AirLoadLength, AirLoadLength, AirLoadLength] = (500, 400, 1000)
    cell_size_cm: FiniteFloat = Field(default=1, ge=.01, le=100)
    irradiation_date: date | None = None


class AirCalibrationComparisonRequest(BaseModel):
    particles: int = Field(default=DEFAULT_STUDY_HISTORIES, ge=100, le=10**12)
    time_limit_minutes: int = Field(default=240, ge=1, le=1440)


class RouteDoseQuery(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    mode: Literal["total", "uniform", "by_position", "target_dose", "rate_sum"]
    run_id: str | None = Field(default=None, max_length=80)
    save: bool = False
    dose_map: bool = False
    value: FiniteFloat | None = None
    times_minutes: list[FiniteFloat] | None = Field(default=None, max_length=100)
    target_metric: Literal["mean", "minimum", "maximum"] = "mean"


class RouteProfileMutation(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    configuration: RouteConfiguration
