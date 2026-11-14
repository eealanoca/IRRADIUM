"""Posprocesamiento eficiente de tallies regulares de OpenMC.

La ruta principal lee únicamente la suma del tally desde ``statepoint.h5`` en
bloques, la convierte a ``float32`` sobre un archivo mapeado y escribe una
malla ``vtkImageData`` comprimida. Así se evita cargar ``sum``, ``sum_sq`` y
``mean`` simultáneamente y se elimina por completo la tetraedrización Delaunay.
"""

from __future__ import annotations

import json
import hashlib
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional, Sequence, Tuple

import h5py
import numpy as np


JOULE_PER_EV = 1.602176634e-19
SECONDS_PER_HOUR = 3600.0

# NIST X-Ray Mass Attenuation Coefficients, Table 4: Alanine.  Keeping the
# complete tabulation is important: photons scattered by the load no longer
# have only the two primary Co-60 energies.
ALANINE_ENERGY_MEV = np.asarray(
    [
        1.0e-3, 1.5e-3, 2.0e-3, 3.0e-3, 4.0e-3, 5.0e-3, 6.0e-3,
        8.0e-3, 1.0e-2, 1.5e-2, 2.0e-2, 3.0e-2, 4.0e-2, 5.0e-2,
        6.0e-2, 8.0e-2, 1.0e-1, 1.5e-1, 2.0e-1, 3.0e-1, 4.0e-1,
        5.0e-1, 6.0e-1, 8.0e-1, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0,
        5.0, 6.0, 8.0, 10.0, 15.0, 20.0,
    ],
    dtype=np.float64,
)
ALANINE_MU_EN_OVER_RHO_CM2_G = np.asarray(
    [
        3.057e3, 1.007e3, 4.456e2, 1.365e2, 5.777e1, 2.934e1,
        1.676e1, 6.853, 3.398, 9.362e-1, 3.743e-1, 1.076e-1,
        5.034e-2, 3.275e-2, 2.643e-2, 2.345e-2, 2.387e-2,
        2.660e-2, 2.871e-2, 3.096e-2, 3.182e-2, 3.202e-2,
        3.187e-2, 3.112e-2, 3.012e-2, 2.878e-2, 2.751e-2,
        2.530e-2, 2.210e-2, 1.996e-2, 1.846e-2, 1.736e-2,
        1.587e-2, 1.493e-2, 1.362e-2, 1.299e-2,
    ],
    dtype=np.float64,
)


def alanine_energy_response():
    """Return the photon-fluence response for dose to pure alanine.

    The energy-function tally scores ``E * mu_en/rho``.  Once divided by the
    mesh volume, its units are eV/g per source photon.  The density of alanine
    must not be applied again because ``mu_en/rho`` is already mass-normalized.
    """

    energy_ev = ALANINE_ENERGY_MEV * 1.0e6
    response = energy_ev * ALANINE_MU_EN_OVER_RHO_CM2_G
    return energy_ev.copy(), response


def material_response_energy_bounds(groups: int = 32) -> np.ndarray:
    """Energy groups used by the short material-response pilot calculation."""

    groups = int(groups)
    if groups < 8:
        raise ValueError("Se requieren al menos ocho grupos de energía")
    return np.geomspace(1.0e3, 2.0e7, groups + 1, dtype=np.float64)


def material_spectral_heating_responses(
    statepoint_file,
    material_names_by_id,
) -> dict:
    """Build smooth material KERMA responses from a pilot statepoint.

    Heating and track-length flux are scored in the same energy groups.  Empty
    or statistically unusable groups are interpolated in log-energy, then the
    response is normalized so its integral reproduces the pilot's total
    material heating exactly.  This removes the old single-response-per-
    material approximation while retaining the lower variance of a
    track-length estimator in the production run.
    """

    import openmc

    with openmc.StatePoint(statepoint_file) as statepoint:
        heating = statepoint.get_tally(name="MaterialEnergyHeating")
        flux = statepoint.get_tally(name="MaterialEnergyFlux")
        total_heating_tally = statepoint.get_tally(name="MaterialTotalHeating")
        total_flux_tally = statepoint.get_tally(name="MaterialTotalFlux")
        material_filter = next(
            item for item in heating.filters if isinstance(item, openmc.MaterialFilter)
        )
        energy_filter = next(
            item for item in heating.filters if isinstance(item, openmc.EnergyFilter)
        )
        energy_bounds = np.asarray(energy_filter.values, dtype=np.float64)
        energy_centers = np.sqrt(energy_bounds[:-1] * energy_bounds[1:])
        expected_shape = (len(material_filter.bins), energy_bounds.size - 1)
        heating_mean = np.asarray(heating.mean, dtype=np.float64).reshape(expected_shape)
        heating_std = np.asarray(heating.std_dev, dtype=np.float64).reshape(expected_shape)
        flux_mean = np.asarray(flux.mean, dtype=np.float64).reshape(expected_shape)
        flux_std = np.asarray(flux.std_dev, dtype=np.float64).reshape(expected_shape)
        total_heating_mean = np.asarray(
            total_heating_tally.mean, dtype=np.float64
        ).reshape(-1)
        total_heating_std = np.asarray(
            total_heating_tally.std_dev, dtype=np.float64
        ).reshape(-1)
        total_flux_mean = np.asarray(
            total_flux_tally.mean, dtype=np.float64
        ).reshape(-1)
        total_flux_std = np.asarray(
            total_flux_tally.std_dev, dtype=np.float64
        ).reshape(-1)

        responses = {}
        for row, material_bin in enumerate(material_filter.bins):
            material_id = int(getattr(material_bin, "id", material_bin))
            name = material_names_by_id.get(material_id)
            if name is None:
                raise KeyError("Material OpenMC sin nombre configurado: %s" % material_id)
            h = np.asarray(heating_mean[row], dtype=np.float64)
            f = np.asarray(flux_mean[row], dtype=np.float64)
            h_err = np.divide(
                heating_std[row], np.abs(h), out=np.full_like(h, np.inf), where=h != 0
            )
            f_err = np.divide(
                flux_std[row], np.abs(f), out=np.full_like(f, np.inf), where=f != 0
            )
            ratio = np.divide(h, f, out=np.full_like(h, np.nan), where=f > 0)
            usable = (
                np.isfinite(ratio)
                & (ratio > 0)
                & np.isfinite(h_err)
                & np.isfinite(f_err)
                & (h_err <= 0.25)
                & (f_err <= 0.25)
            )
            if np.count_nonzero(usable) < 2:
                usable = np.isfinite(ratio) & (ratio > 0)
            if np.count_nonzero(usable) < 1:
                raise ValueError("El piloto no obtuvo respuesta espectral para %s" % name)
            if np.count_nonzero(usable) == 1:
                smoothed = np.full_like(ratio, ratio[usable][0])
            else:
                smoothed = np.exp(
                    np.interp(
                        np.log(energy_centers),
                        np.log(energy_centers[usable]),
                        np.log(ratio[usable]),
                    )
                )
            predicted = float(np.sum(f * smoothed))
            # La respuesta debe conservar el tally heating total, que también
            # incluye la energía depositada atribuida al fotón fuera de los
            # grupos explícitos. Usar sum(h) introducía un sesgo bajo cuando
            # el EnergyFilter no cubría toda esa contribución.
            observed = float(total_heating_mean[row])
            if predicted <= 0 or observed <= 0:
                raise ValueError("La respuesta integrada no es positiva para %s" % name)
            smoothed *= observed / predicted
            total_heating_error = float(
                total_heating_std[row] / abs(total_heating_mean[row])
            )
            integrated_flux = float(np.sum(f))
            total_flux_error = float(total_flux_std[row] / abs(total_flux_mean[row]))
            responses[str(name).strip().lower()] = {
                "openmc_material_id": material_id,
                "energy_ev": energy_bounds.tolist(),
                # histogram interpolation uses the left value in every group;
                # duplicate the final group value for the final grid point.
                "heating_per_tracklength_ev_per_cm": np.concatenate(
                    (smoothed, smoothed[-1:])
                ).tolist(),
                "relative_error": float(
                    np.hypot(total_heating_error, total_flux_error)
                ),
                "usable_energy_groups": int(np.count_nonzero(usable)),
                "energy_groups": int(energy_centers.size),
                "pilot_heating_per_source_ev": observed,
                "pilot_tracklength_per_source_cm": integrated_flux,
                "normalization_correction": float(observed / predicted),
            }
    return responses


def material_dose_calibration_factors(
    statepoint_file,
    material_names_by_id,
    configured_materials,
) -> dict:
    """Calibrate a low-variance alanine response to dose in each material.

    For each load material the dimensionless factor is
    ``heating / (density * alanine_response)``.  Multiplying the production
    fluence response by it makes its material-average dose reproduce direct
    OpenMC heating without carrying a high-variance heating score in every
    spatial cell.
    """

    import openmc

    density_by_name = {
        str(item["name"]).strip().lower(): float(item["density"])
        for item in configured_materials
    }
    with openmc.StatePoint(statepoint_file) as statepoint:
        heating = statepoint.get_tally(name="FastCalibrationHeating")
        alanine = statepoint.get_tally(name="FastCalibrationAlanine")
        material_filter = next(
            item for item in heating.filters if isinstance(item, openmc.MaterialFilter)
        )
        heating_mean = np.asarray(heating.mean, dtype=np.float64).reshape(-1)
        heating_std = np.asarray(heating.std_dev, dtype=np.float64).reshape(-1)
        alanine_mean = np.asarray(alanine.mean, dtype=np.float64).reshape(-1)
        alanine_std = np.asarray(alanine.std_dev, dtype=np.float64).reshape(-1)
        factors = {}
        for row, material_bin in enumerate(material_filter.bins):
            material_id = int(getattr(material_bin, "id", material_bin))
            name = material_names_by_id.get(material_id)
            density = density_by_name.get(name)
            if density is None or not np.isfinite(density) or density <= 0:
                raise ValueError("Falta una densidad válida para el material %s" % name)
            h = float(heating_mean[row])
            a = float(alanine_mean[row])
            if not np.isfinite(h) or not np.isfinite(a) or h <= 0 or a <= 0:
                raise ValueError("La calibración rápida no convergió para %s" % name)
            relative_error = float(
                np.hypot(heating_std[row] / h, alanine_std[row] / a)
            )
            factors[name] = {
                "factor": h / (density * a),
                "relative_error": relative_error,
                "density_g_cm3": density,
                "heating_ev_per_source": h,
                "alanine_response_integral": a,
            }
    return factors


def high_dose_material_calibration_factors(
    statepoint_file,
    heating_tally_names,
    response_tally_names,
    configured_materials,
    mesh: RegularMeshSpec,
    material_fractions: np.ndarray,
    material_labels,
    upper_fraction: float = 0.10,
    curve_bins: int = 20,
) -> dict:
    """Build a per-material low-variance response-to-heating curve.

    Coarse cells are ordered by their alanine response and aggregated in
    quantile groups before fitting a monotonic log-log curve to direct heating.
    It captures the systematic change of spectrum from the load surface to its
    shielded interior without fitting a noisy correction in every cell.
    """

    import openmc

    upper_fraction = float(upper_fraction)
    if not 0 < upper_fraction <= 0.5:
        raise ValueError("La fracción de calibración alta no es válida")
    curve_bins = int(curve_bins)
    if curve_bins < 4:
        raise ValueError("La curva de calibración requiere al menos cuatro grupos")
    density_by_name = {
        str(item["name"]).strip().lower(): float(item["density"])
        for item in configured_materials
    }
    fractions = np.asarray(material_fractions, dtype=np.float64)
    if fractions.shape != (len(material_labels), mesh.cell_count):
        raise ValueError("Las fracciones de calibración no coinciden con la malla")
    coverage = np.sum(fractions, axis=0, dtype=np.float64)
    effective_density = np.zeros(mesh.cell_count, dtype=np.float64)
    for row, label in enumerate(material_labels):
        name = str(label["name"]).strip().lower()
        density = density_by_name.get(name)
        if density is None or not np.isfinite(density) or density <= 0:
            raise ValueError("Falta una densidad válida para el material %s" % name)
        effective_density += fractions[row] * density
    dominant_material = np.argmax(fractions, axis=0)
    total_heating = np.zeros(mesh.cell_count, dtype=np.float64)
    total_heating_variance = np.zeros(mesh.cell_count, dtype=np.float64)
    total_response = np.zeros(mesh.cell_count, dtype=np.float64)
    total_response_variance = np.zeros(mesh.cell_count, dtype=np.float64)
    with openmc.StatePoint(statepoint_file) as statepoint:
        for name, heating_name in heating_tally_names.items():
            response_name = response_tally_names.get(name)
            if response_name is None:
                raise KeyError("Falta el tally de respuesta para %s" % name)
            heating = statepoint.get_tally(name=heating_name)
            response = statepoint.get_tally(name=response_name)
            h = np.asarray(heating.mean, dtype=np.float64).reshape(-1)
            h_std = np.asarray(heating.std_dev, dtype=np.float64).reshape(-1)
            a = np.asarray(response.mean, dtype=np.float64).reshape(-1)
            a_std = np.asarray(response.std_dev, dtype=np.float64).reshape(-1)
            heating_filter = next(
                item
                for item in heating.filters
                if isinstance(item, openmc.MeshMaterialFilter)
            )
            response_filter = next(
                item
                for item in response.filters
                if isinstance(item, openmc.MeshMaterialFilter)
            )
            heating_indices = np.asarray(heating_filter.bins, dtype=np.int64)[:, 0]
            response_indices = np.asarray(response_filter.bins, dtype=np.int64)[:, 0]
            if (
                h.size != a.size
                or h.size != heating_indices.size
                or not np.array_equal(heating_indices, response_indices)
            ):
                raise ValueError("Los tallies de calibración espacial no coinciden")
            total_heating[heating_indices] += h
            total_heating_variance[heating_indices] += np.square(h_std)
            total_response[response_indices] += a
            total_response_variance[response_indices] += np.square(a_std)

    result = {}
    occupied_volume = coverage * mesh.cell_volume
    occupied_mass = effective_density * mesh.cell_volume
    for row, label in enumerate(material_labels):
        name = str(label["name"]).strip().lower()
        valid = (
            (dominant_material == row)
            & (coverage >= 0.5)
            & np.isfinite(total_heating)
            & np.isfinite(total_response)
            & (total_heating >= 0)
            & (total_response > 0)
            & (occupied_volume > 0)
            & (occupied_mass > 0)
        )
        if not np.any(valid):
            raise ValueError("La calibración alta no obtuvo datos para %s" % name)
        response_density_all = np.zeros(mesh.cell_count, dtype=np.float64)
        heating_density_all = np.zeros(mesh.cell_count, dtype=np.float64)
        response_density_all[valid] = total_response[valid] / occupied_volume[valid]
        heating_density_all[valid] = total_heating[valid] / occupied_mass[valid]
        ordered = np.flatnonzero(valid)[np.argsort(response_density_all[valid])]
        groups = [
            group for group in np.array_split(ordered, curve_bins) if group.size
        ]
        x_values = []
        y_values = []
        errors = []
        counts = []
        for group in groups:
            a_sum = float(np.sum(total_response[group], dtype=np.float64))
            h_sum = float(np.sum(total_heating[group], dtype=np.float64))
            if a_sum <= 0 or h_sum <= 0:
                continue
            x_values.append(float(np.mean(response_density_all[group])))
            y_values.append(float(np.mean(heating_density_all[group])))
            errors.append(
                float(
                    np.hypot(
                        np.sqrt(np.sum(total_heating_variance[group])) / h_sum,
                        np.sqrt(np.sum(total_response_variance[group])) / a_sum,
                    )
                )
            )
            counts.append(int(group.size))
        if len(x_values) < 4:
            raise ValueError("La curva rápida no convergió para %s" % name)
        response_density = response_density_all[valid]
        heating_density = heating_density_all[valid]
        # Una regresión por medias condicionadas reproduce bien el centro de
        # la distribución, pero suaviza precisamente la cola que contiene la
        # dosis máxima. La transformación monótona por cuantiles conserva el
        # orden espacial del estimador de baja varianza y hace coincidir su
        # distribución con la deposición directa observada en el piloto. Los
        # puntos se concentran en P90-P99.9 para estabilizar la zona de dosis
        # alta sin ajustar al máximo, que es demasiado sensible al ruido.
        calibration_quantiles = np.concatenate(
            (
                np.linspace(0.02, 0.90, max(4, curve_bins - 6)),
                np.asarray((0.93, 0.95, 0.98, 0.99, 0.995, 0.999)),
            )
        )
        calibration_quantiles = np.unique(
            np.clip(calibration_quantiles, 0.0, 0.999)
        )
        x_values = np.quantile(response_density, calibration_quantiles)
        y_values = np.quantile(heating_density, calibration_quantiles)
        positive = (x_values > 0) & (y_values > 0)
        x_values = np.asarray(x_values[positive], dtype=np.float64)
        y_values = np.maximum.accumulate(
            np.asarray(y_values[positive], dtype=np.float64)
        )
        calibration_quantiles = calibration_quantiles[positive]
        x_values, unique_indices = np.unique(x_values, return_index=True)
        y_values = y_values[unique_indices]
        calibration_quantiles = calibration_quantiles[unique_indices]
        if len(x_values) < 4:
            raise ValueError(
                "La curva rápida no tiene suficientes valores para %s" % name
            )
        tail_ratios = [
            float(
                np.quantile(heating_density, quantile)
                / np.quantile(response_density, quantile)
            )
            for quantile in (0.95, 0.98, 0.99, 0.995, 0.999)
        ]
        tail_factor = float(np.median(tail_ratios))
        tail_correction = 1.0
        high_group_count = max(1, int(np.ceil(len(x_values) * upper_fraction)))
        high_error = float(max(errors[-high_group_count:]))
        result[name] = {
            "factor": tail_factor,
            "relative_error": high_error,
            "response_per_cm3": x_values.tolist(),
            "heating_per_gram": y_values.tolist(),
            "calibration_quantiles": calibration_quantiles.tolist(),
            "group_relative_error": errors,
            "group_cells": counts,
            "selected_cells": int(sum(counts[-high_group_count:])),
            "upper_fraction": upper_fraction,
            "tail_quantile_ratios": tail_ratios,
            "tail_correction": tail_correction,
        }
    return result


@dataclass(frozen=True)
class RegularMeshSpec:
    """Geometría cartesiana exacta del ``RegularMesh`` de OpenMC."""

    dimension: Tuple[int, int, int]
    lower_left: Tuple[float, float, float]
    upper_right: Tuple[float, float, float]

    def __post_init__(self) -> None:
        if len(self.dimension) != 3 or any(int(value) <= 0 for value in self.dimension):
            raise ValueError("La malla Monte Carlo debe tener tres dimensiones positivas")
        if len(self.lower_left) != 3 or len(self.upper_right) != 3:
            raise ValueError("Los límites de la malla deben contener X, Y y Z")
        if any(high <= low for low, high in zip(self.lower_left, self.upper_right)):
            raise ValueError("Cada límite superior debe ser mayor que el inferior")

    @property
    def cell_count(self) -> int:
        return int(np.prod(self.dimension, dtype=np.int64))

    @property
    def spacing(self) -> Tuple[float, float, float]:
        return tuple(
            (high - low) / cells
            for low, high, cells in zip(self.lower_left, self.upper_right, self.dimension)
        )

    @property
    def cell_volume(self) -> float:
        return float(np.prod(self.spacing))


def mesh_spec_from_openmc(mesh) -> RegularMeshSpec:
    """Convierte un ``openmc.RegularMesh`` sin retener objetos de OpenMC."""

    return RegularMeshSpec(
        dimension=tuple(int(value) for value in mesh.dimension),
        lower_left=tuple(float(value) for value in mesh.lower_left),
        upper_right=tuple(float(value) for value in mesh.upper_right),
    )


def heating_dose_rate_scale(
    material_ids: np.ndarray,
    material_labels,
    configured_materials,
    mesh: RegularMeshSpec,
    photon_yield: float = 1.0,
    material_fractions: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Devuelve el factor por celda para convertir ``heating`` a kGy/h.

    El tally ``heating`` aporta energía depositada en eV por partícula fuente.
    En este modelo ``Source.strength`` contiene la actividad, por lo que el
    resultado ya queda normalizado temporalmente. Numéricamente, J/g equivale
    a kGy; por ello se convierte eV→J, se multiplica por s/h y se divide por la
    masa de cada celda ``densidad[g/cm³] × volumen[cm³]``.
    """

    ids = np.asarray(material_ids).reshape(-1)
    if ids.size != mesh.cell_count:
        raise ValueError("Los identificadores de material no coinciden con la malla")
    photon_yield = float(photon_yield)
    if not np.isfinite(photon_yield) or photon_yield <= 0:
        raise ValueError("El rendimiento fotónico debe ser positivo")

    density_by_name = {
        str(material["name"]).strip().lower(): float(material["density"])
        for material in configured_materials
    }
    scale = np.zeros(mesh.cell_count, dtype=np.float64)
    base = JOULE_PER_EV * SECONDS_PER_HOUR * photon_yield / mesh.cell_volume
    if material_fractions is not None:
        fractions = np.asarray(material_fractions, dtype=np.float64)
        if fractions.shape != (len(material_labels), mesh.cell_count):
            raise ValueError("Las fracciones de material no coinciden con la malla")
        effective_density = np.zeros(mesh.cell_count, dtype=np.float64)
        for row, label in enumerate(material_labels):
            name = str(label["name"]).strip().lower()
            density = density_by_name.get(name)
            if density is None or not np.isfinite(density) or density <= 0:
                raise ValueError("Falta una densidad válida para el material %s" % name)
            effective_density += fractions[row] * density
        occupied = effective_density > 0
        scale[occupied] = base / effective_density[occupied]
        return scale
    for label in material_labels:
        name = str(label["name"]).strip().lower()
        density = density_by_name.get(name)
        if density is None or not np.isfinite(density) or density <= 0:
            raise ValueError("Falta una densidad válida para el material %s" % name)
        scale[ids == int(label["id"])] = base / density
    return scale


def material_heating_responses(statepoint_file, material_names_by_id) -> dict:
    """Obtiene la respuesta heating/flujo de cada material de carga.

    ``heating`` aporta eV por partícula fuente y ``flux`` longitud de
    trayectoria en cm por partícula fuente. Su cociente es una respuesta
    macroscópica efectiva [eV/cm] calculada con el espectro transportado por
    OpenMC, no un coeficiente externo constante.
    """

    import openmc

    with openmc.StatePoint(statepoint_file) as statepoint:
        heating = statepoint.get_tally(name="HeatingByMaterial")
        flux = statepoint.get_tally(name="FluxByMaterial")
        material_filter = next(
            item for item in heating.filters if isinstance(item, openmc.MaterialFilter)
        )
        heating_mean = np.asarray(heating.mean, dtype=np.float64).reshape(-1)
        heating_std = np.asarray(heating.std_dev, dtype=np.float64).reshape(-1)
        flux_mean = np.asarray(flux.mean, dtype=np.float64).reshape(-1)
        flux_std = np.asarray(flux.std_dev, dtype=np.float64).reshape(-1)
        if not (
            heating_mean.size
            == heating_std.size
            == flux_mean.size
            == flux_std.size
            == len(material_filter.bins)
        ):
            raise ValueError("Los tallies híbridos por material no son compatibles")
        responses = {}
        for index, material_bin in enumerate(material_filter.bins):
            material_id = int(getattr(material_bin, "id", material_bin))
            name = material_names_by_id.get(material_id)
            if name is None:
                raise KeyError("Material OpenMC sin nombre configurado: %s" % material_id)
            if heating_mean[index] <= 0 or flux_mean[index] <= 0:
                raise ValueError("Respuesta heating/flujo no positiva para %s" % name)
            heating_relative_error = heating_std[index] / abs(heating_mean[index])
            flux_relative_error = flux_std[index] / abs(flux_mean[index])
            responses[str(name).strip().lower()] = {
                "openmc_material_id": material_id,
                "heating_per_source_ev": float(heating_mean[index]),
                "photon_tracklength_per_source_cm": float(flux_mean[index]),
                "heating_per_tracklength_ev_per_cm": float(
                    heating_mean[index] / flux_mean[index]
                ),
                # Se informa una combinación conservadora sin asumir la
                # covarianza favorable entre ambos tallies correlacionados.
                "relative_error": float(
                    np.hypot(heating_relative_error, flux_relative_error)
                ),
            }
    return responses


def hybrid_heating_dose_rate_scale(
    material_ids: np.ndarray,
    material_labels,
    configured_materials,
    mesh: RegularMeshSpec,
    material_responses,
    photon_yield: float = 1.0,
    material_fractions: Optional[np.ndarray] = None,
):
    """Convierte flujo fino a dosis usando respuestas ``heating`` de OpenMC.

    Devuelve tanto el factor de dosis por celda como la incertidumbre relativa
    adicional asociada al cociente heating/flujo de cada material.
    """

    base_scale = heating_dose_rate_scale(
        material_ids,
        material_labels,
        configured_materials,
        mesh,
        photon_yield=photon_yield,
        material_fractions=material_fractions,
    )
    response = np.zeros(mesh.cell_count, dtype=np.float64)
    response_error = np.zeros(mesh.cell_count, dtype=np.float64)
    ids = np.asarray(material_ids).reshape(-1)
    fractions = None
    if material_fractions is not None:
        fractions = np.asarray(material_fractions, dtype=np.float64)
    for row, label in enumerate(material_labels):
        name = str(label["name"]).strip().lower()
        details = material_responses.get(name)
        if details is None:
            raise KeyError("Falta la respuesta heating para el material %s" % name)
        value = float(details["heating_per_tracklength_ev_per_cm"])
        error = float(details["relative_error"])
        if fractions is None:
            selected = ids == int(label["id"])
            response[selected] = value
            response_error[selected] = error
        else:
            response += fractions[row] * value
            response_error = np.maximum(response_error, fractions[row] * error)
    return base_scale * response, response_error


def sparse_mesh_material_bins(
    material_fractions: np.ndarray,
    material_labels,
    openmc_materials_by_name,
    minimum_fraction: float = 0.0,
) -> dict:
    """Return compact mesh/material pairs keyed by configured material name."""

    fractions = np.asarray(material_fractions, dtype=np.float64)
    if fractions.ndim != 2 or fractions.shape[0] != len(material_labels):
        raise ValueError("Las fracciones de material no son compatibles")
    minimum_fraction = float(minimum_fraction)
    if minimum_fraction < 0 or minimum_fraction >= 1:
        raise ValueError("La fracción mínima debe estar entre cero y uno")
    result = {}
    for row, label in enumerate(material_labels):
        name = str(label["name"]).strip().lower()
        material = openmc_materials_by_name.get(name)
        if material is None:
            raise KeyError("Material OpenMC no encontrado: %s" % name)
        indices = np.flatnonzero(fractions[row] > minimum_fraction).astype(np.int64)
        if indices.size:
            result[name] = {
                "material": material,
                "bins": np.column_stack(
                    (indices, np.full(indices.size, int(material.id), dtype=np.int64))
                ),
            }
    if not result:
        raise ValueError("La malla no contiene celdas de material")
    return result


def sparse_mesh_material_bins_from_ids(
    material_ids: np.ndarray,
    material_labels,
    openmc_materials_by_name,
) -> dict:
    """Return compact mesh/material pairs for centre-classified load cells.

    This is used by the fast estimator so its tally contains only cells inside
    the load.  It avoids making the inexpensive mode pay for sub-voxel volume
    fractions while also keeping empty world cells out of OpenMC triggers.
    """

    ids = np.asarray(material_ids).reshape(-1)
    result = {}
    for label in material_labels:
        name = str(label["name"]).strip().lower()
        material = openmc_materials_by_name.get(name)
        if material is None:
            raise KeyError("Material OpenMC no encontrado: %s" % name)
        indices = np.flatnonzero(ids == int(label["id"])).astype(np.int64)
        if indices.size:
            result[name] = {
                "material": material,
                "bins": np.column_stack(
                    (indices, np.full(indices.size, int(material.id), dtype=np.int64))
                ),
            }
    if not result:
        raise ValueError("La malla no contiene celdas de material")
    return result


def sparse_response_dose_statistics(
    statepoint_file,
    tally_names,
    mesh: RegularMeshSpec,
    dose_scale: float,
    occupied_fraction=None,
    material_response_errors=None,
    material_calibration_curves=None,
    calibration_material_ids=None,
    calibration_material_labels=None,
):
    """Combine material-restricted fluence-response tallies into dose.

    ``dose_scale`` converts the response integral per cubic centimetre to
    kGy/h.  When sub-voxel fractions are supplied, the denominator is the
    actual occupied load volume; otherwise the full mesh-cell volume preserves
    the fast estimator's centre-classified convention.
    """

    import openmc

    response = np.zeros(mesh.cell_count, dtype=np.float64)
    variance = np.zeros(mesh.cell_count, dtype=np.float64)
    response_error = np.zeros(mesh.cell_count, dtype=np.float64)
    indices_by_material = {}
    with openmc.StatePoint(statepoint_file) as statepoint:
        realizations = None
        for material_name, tally_name in tally_names.items():
            tally = statepoint.get_tally(name=tally_name)
            mesh_material_filter = next(
                item
                for item in tally.filters
                if isinstance(item, openmc.MeshMaterialFilter)
            )
            bins = np.asarray(mesh_material_filter.bins, dtype=np.int64)
            indices = bins[:, 0]
            indices_by_material[material_name] = indices
            means = np.asarray(tally.mean, dtype=np.float64).reshape(-1)
            standard_deviation = np.asarray(tally.std_dev, dtype=np.float64).reshape(-1)
            if means.size != indices.size:
                raise ValueError("El tally disperso no coincide con sus celdas")
            response[indices] += means
            variance[indices] += np.square(standard_deviation)
            if material_response_errors:
                response_error[indices] = np.maximum(
                    response_error[indices],
                    float(material_response_errors.get(material_name, 0.0)),
                )
            if realizations is None:
                realizations = int(tally.num_realizations)
            elif realizations != int(tally.num_realizations):
                raise ValueError("Los tallies materiales no tienen las mismas realizaciones")

    if occupied_fraction is None:
        denominator = np.full(mesh.cell_count, mesh.cell_volume, dtype=np.float64)
    else:
        fraction = np.asarray(occupied_fraction, dtype=np.float64).reshape(-1)
        if fraction.size != mesh.cell_count:
            raise ValueError("La fracción ocupada no coincide con la malla")
        denominator = fraction * mesh.cell_volume
    dose = np.zeros(mesh.cell_count, dtype=np.float32)
    relative_error = np.full(mesh.cell_count, np.nan, dtype=np.float32)
    occupied = denominator > 0
    response_density = np.zeros(mesh.cell_count, dtype=np.float64)
    response_density[occupied] = response[occupied] / denominator[occupied]
    if material_calibration_curves:
        calibration_ids = None
        calibration_id_by_name = {}
        if calibration_material_ids is not None:
            calibration_ids = np.asarray(calibration_material_ids).reshape(-1)
            if calibration_ids.size != mesh.cell_count:
                raise ValueError("Los materiales de calibración no coinciden con la malla")
            calibration_id_by_name = {
                str(label["name"]).strip().lower(): int(label["id"])
                for label in (calibration_material_labels or [])
            }
        for material_name, indices in indices_by_material.items():
            curve = material_calibration_curves.get(material_name)
            if not curve:
                continue
            if calibration_ids is None:
                selected = indices[response_density[indices] > 0]
            else:
                material_id = calibration_id_by_name.get(material_name)
                if material_id is None:
                    raise KeyError("Material de calibración desconocido: %s" % material_name)
                selected = np.flatnonzero(
                    (calibration_ids == material_id) & (response_density > 0)
                )
            if selected.size == 0:
                continue
            x = np.asarray(curve["response_per_cm3"], dtype=np.float64)
            y = np.asarray(curve["heating_per_gram"], dtype=np.float64)
            values = response_density[selected]
            transformed = np.empty_like(values)
            below = values <= x[0]
            above = values >= x[-1]
            middle = ~(below | above)
            transformed[below] = values[below] * (y[0] / x[0])
            transformed[above] = values[above] * (y[-1] / x[-1])
            transformed[middle] = np.exp(
                np.interp(np.log(values[middle]), np.log(x), np.log(y))
            )
            response_density[selected] = transformed
    dose[occupied] = (response_density[occupied] * float(dose_scale)).astype(np.float32)
    nonzero = occupied & (response != 0)
    relative_error[nonzero] = (
        np.sqrt(variance[nonzero]) / np.abs(response[nonzero])
    ).astype(np.float32)
    if material_response_errors:
        np.hypot(relative_error, response_error, out=relative_error)
    return dose, relative_error, int(realizations or 0)


def process_sparse_response_tallies_to_artifacts(
    statepoint_file,
    output_root,
    tally_names,
    mesh: RegularMeshSpec,
    dose_scale: float,
    inside_mask,
    material_ids,
    material_labels,
    occupied_fraction=None,
    material_response_errors=None,
    material_calibration_curves=None,
    tally_name="LoadResponse",
    vtk_filename="dose_distribution.vti",
) -> dict:
    """Create VTI and section artifacts from sparse load-only responses."""

    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    dose, relative_error, realizations = sparse_response_dose_statistics(
        statepoint_file,
        tally_names,
        mesh,
        dose_scale,
        occupied_fraction=occupied_fraction,
        material_response_errors=material_response_errors,
        material_calibration_curves=material_calibration_curves,
        calibration_material_ids=(
            material_ids if material_calibration_curves else None
        ),
        calibration_material_labels=(
            material_labels if material_calibration_curves else None
        ),
    )
    result = write_structured_dose_artifacts(
        dose,
        mesh,
        root,
        inside_mask=inside_mask,
        material_ids=material_ids,
        material_labels=material_labels,
        relative_error=relative_error,
        vtk_filename=vtk_filename,
    )
    result.update(
        {
            "tally_name": tally_name,
            "tally_names": dict(tally_names),
            "num_realizations": realizations,
            "dose_coefficient": float(dose_scale),
            "postprocess": "sparse-load-response-vti",
        }
    )
    result["visualization"] = {
        key: value for key, value in result.items() if key != "visualization"
    }
    with (root / "result.json").open("w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    return result


def summarize_sparse_response_tallies(
    statepoint_file,
    tally_names,
    mesh: RegularMeshSpec,
    dose_scale: float,
    inside_mask,
    occupied_fraction=None,
    material_response_errors=None,
    material_calibration_curves=None,
    calibration_material_ids=None,
    calibration_material_labels=None,
    tally_name="LoadResponseEvaluation",
) -> dict:
    """Summarize a sparse load-only response without writing another VTI."""

    dose, relative_error, realizations = sparse_response_dose_statistics(
        statepoint_file,
        tally_names,
        mesh,
        dose_scale,
        occupied_fraction=occupied_fraction,
        material_response_errors=material_response_errors,
        material_calibration_curves=material_calibration_curves,
        calibration_material_ids=calibration_material_ids,
        calibration_material_labels=calibration_material_labels,
    )
    result = summarize_dose(dose, mesh, inside_mask=inside_mask)
    result.update(summarize_relative_error(relative_error, inside_mask=inside_mask))
    result.update(
        {
            "tally_name": tally_name,
            "tally_names": dict(tally_names),
            "num_realizations": realizations,
            "mesh": asdict(mesh),
            "cell_size_cm": list(mesh.spacing),
            "method": "sparse-load-response-tally",
        }
    )
    return result


def material_mixture_mass_g(
    material_fractions: np.ndarray,
    material_labels,
    configured_materials,
    mesh: RegularMeshSpec,
) -> np.ndarray:
    """Mass of load material represented in each mesh cell, in grams."""

    fractions = np.asarray(material_fractions, dtype=np.float64)
    if fractions.shape != (len(material_labels), mesh.cell_count):
        raise ValueError("Las fracciones de material no coinciden con la malla")
    density_by_name = {
        str(material["name"]).strip().lower(): float(material["density"])
        for material in configured_materials
    }
    mass = np.zeros(mesh.cell_count, dtype=np.float64)
    for row, label in enumerate(material_labels):
        name = str(label["name"]).strip().lower()
        density = density_by_name.get(name)
        if density is None or not np.isfinite(density) or density <= 0:
            raise ValueError("Falta una densidad válida para el material %s" % name)
        mass += fractions[row] * density * mesh.cell_volume
    return mass


def sparse_material_dose_statistics(
    statepoint_file,
    tally_names,
    mesh: RegularMeshSpec,
    material_fractions,
    material_labels,
    configured_materials,
    photon_yield: float,
    material_response_errors=None,
):
    """Combine sparse material-resolved energy tallies into dose and error.

    Each tally already contains the material-specific spectral response and
    therefore estimates deposited energy [eV/source].  Summing energy first and
    dividing by the actual mixture mass makes boundary cells independent of an
    arbitrary dominant-material label.
    """

    import openmc

    energy = np.zeros(mesh.cell_count, dtype=np.float64)
    variance = np.zeros(mesh.cell_count, dtype=np.float64)
    response_error = np.zeros(mesh.cell_count, dtype=np.float64)
    fractions = np.asarray(material_fractions, dtype=np.float64)
    label_row = {
        str(label["name"]).strip().lower(): row
        for row, label in enumerate(material_labels)
    }
    with openmc.StatePoint(statepoint_file) as statepoint:
        realizations = None
        for material_name, tally_name in tally_names.items():
            tally = statepoint.get_tally(name=tally_name)
            mesh_material_filter = next(
                item
                for item in tally.filters
                if isinstance(item, openmc.MeshMaterialFilter)
            )
            bins = np.asarray(mesh_material_filter.bins, dtype=np.int64)
            indices = bins[:, 0]
            means = np.asarray(tally.mean, dtype=np.float64).reshape(-1)
            standard_deviation = np.asarray(tally.std_dev, dtype=np.float64).reshape(-1)
            if means.size != indices.size:
                raise ValueError("El tally disperso no coincide con sus celdas")
            energy[indices] += means
            variance[indices] += np.square(standard_deviation)
            if material_response_errors:
                row = label_row[material_name]
                selected_error = float(material_response_errors.get(material_name, 0.0))
                response_error[indices] = np.maximum(
                    response_error[indices], fractions[row, indices] * selected_error
                )
            if realizations is None:
                realizations = int(tally.num_realizations)
            elif realizations != int(tally.num_realizations):
                raise ValueError("Los tallies materiales no tienen las mismas realizaciones")

    mass = material_mixture_mass_g(
        fractions, material_labels, configured_materials, mesh
    )
    dose = np.zeros(mesh.cell_count, dtype=np.float32)
    relative_error = np.full(mesh.cell_count, np.nan, dtype=np.float32)
    occupied = mass > 0
    scale = JOULE_PER_EV * SECONDS_PER_HOUR * float(photon_yield)
    dose[occupied] = (energy[occupied] * scale / mass[occupied]).astype(np.float32)
    nonzero = occupied & (energy != 0)
    relative_error[nonzero] = (
        np.sqrt(variance[nonzero]) / np.abs(energy[nonzero])
    ).astype(np.float32)
    if material_response_errors:
        np.hypot(relative_error, response_error, out=relative_error)
    return dose, relative_error, int(realizations or 0)


def tally_absolute_standard_deviation(values, relative_error, realizations):
    """Keep OpenMC's absolute zero-batch variance, not an undefined 0/0 RSE."""
    values, relative_error = np.asarray(values), np.asarray(relative_error)
    absolute = np.full(values.shape, np.nan, dtype=np.float32)
    valid = np.isfinite(relative_error) & (relative_error >= 0)
    absolute[valid] = np.abs(values[valid]) * relative_error[valid]
    if realizations >= 2:
        absolute[(values == 0) & np.isnan(relative_error)] = 0
    return absolute


def process_sparse_material_tallies_to_artifacts(
    statepoint_file,
    output_root,
    tally_names,
    mesh: RegularMeshSpec,
    material_fractions,
    material_ids,
    material_labels,
    configured_materials,
    photon_yield: float,
    inside_mask,
    material_response_errors=None,
    tally_name="MaterialHeating",
    vtk_filename="dose_distribution.vti",
) -> dict:
    """Create the normal artifacts from compact material-energy tallies."""

    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    dose, relative_error, realizations = sparse_material_dose_statistics(
        statepoint_file,
        tally_names,
        mesh,
        material_fractions,
        material_labels,
        configured_materials,
        photon_yield,
        material_response_errors=material_response_errors,
    )
    result = write_structured_dose_artifacts(
        dose,
        mesh,
        root,
        inside_mask=inside_mask,
        material_ids=material_ids,
        material_labels=material_labels,
        relative_error=relative_error,
        vtk_filename=vtk_filename,
        absolute_std_dev=tally_absolute_standard_deviation(dose, relative_error, realizations),
    )
    result.update(
        {
            "tally_name": tally_name,
            "tally_names": dict(tally_names),
            "num_realizations": realizations,
            "dose_coefficient": None,
            "postprocess": "sparse-material-heating-vti",
        }
    )
    result["visualization"] = {
        key: value for key, value in result.items() if key != "visualization"
    }
    with (root / "result.json").open("w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    return result


def summarize_sparse_material_tallies(
    statepoint_file,
    tally_names,
    mesh: RegularMeshSpec,
    material_fractions,
    material_labels,
    configured_materials,
    photon_yield: float,
    inside_mask,
    material_response_errors=None,
    tally_name="MaterialHeatingEvaluation",
) -> dict:
    dose, relative_error, realizations = sparse_material_dose_statistics(
        statepoint_file,
        tally_names,
        mesh,
        material_fractions,
        material_labels,
        configured_materials,
        photon_yield,
        material_response_errors=material_response_errors,
    )
    result = summarize_dose(dose, mesh, inside_mask=inside_mask)
    result.update(summarize_relative_error(relative_error, inside_mask=inside_mask))
    result.update(
        {
            "tally_name": tally_name,
            "tally_names": dict(tally_names),
            "num_realizations": realizations,
            "mesh": asdict(mesh),
            "cell_size_cm": list(mesh.spacing),
            "method": "sparse-material-heating-tally",
        }
    )
    return result


def _scale_values(scale, expected_cells: int):
    if np.isscalar(scale):
        return float(scale)
    values = np.asarray(scale, dtype=np.float64).reshape(-1)
    if values.size != expected_cells:
        raise ValueError("El factor de conversión por celda no coincide con la malla")
    return values


def read_tally_mean_chunked(
    statepoint_file: os.PathLike,
    tally_id: int,
    num_realizations: int,
    expected_cells: int,
    destination: os.PathLike,
    scale: float = 1.0,
    score_index: int = 0,
    chunk_cells: int = 1_000_000,
) -> np.memmap:
    """Lee solo ``sum`` desde HDF5 y produce la media escalada en ``float32``.

    ``OpenMC.Tally.mean`` lee también ``sum_sq`` y crea una tercera matriz. Esta
    función mantiene en RAM únicamente un bloque temporal y deja el volumen
    completo respaldado por un archivo mapeado, que puede paginarse a swap de
    forma natural si el sistema lo necesita.
    """

    if num_realizations <= 0:
        raise ValueError("El tally no contiene realizaciones válidas")
    if expected_cells <= 0:
        raise ValueError("La malla no contiene celdas")
    if chunk_cells <= 0:
        raise ValueError("chunk_cells debe ser positivo")

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    dose = np.memmap(destination, dtype=np.float32, mode="w+", shape=(expected_cells,))
    dataset_path = "tallies/tally %s/results" % int(tally_id)
    scale_values = _scale_values(scale, expected_cells)

    with h5py.File(statepoint_file, "r") as statepoint:
        if dataset_path not in statepoint:
            raise KeyError("No existe %s en el statepoint" % dataset_path)
        results = statepoint[dataset_path]
        if results.ndim != 3 or results.shape[2] < 1:
            raise ValueError("El dataset de resultados OpenMC tiene una forma incompatible")
        if results.shape[0] != expected_cells:
            raise ValueError(
                "El tally contiene %s bins y la malla requiere %s"
                % (results.shape[0], expected_cells)
            )
        if score_index < 0 or score_index >= results.shape[1]:
            raise ValueError("El índice de score no existe en el tally")

        for start in range(0, expected_cells, chunk_cells):
            stop = min(start + chunk_cells, expected_cells)
            block = results[start:stop, score_index, 0]
            block_scale = (
                scale_values
                if np.isscalar(scale_values)
                else scale_values[start:stop]
            )
            np.multiply(block, block_scale / float(num_realizations), out=block)
            dose[start:stop] = block

    dose.flush()
    return dose


def read_tally_statistics_chunked(
    statepoint_file: os.PathLike,
    tally_id: int,
    num_realizations: int,
    expected_cells: int,
    mean_destination: os.PathLike,
    relative_error_destination: os.PathLike,
    scale: float = 1.0,
    score_index: int = 0,
    chunk_cells: int = 1_000_000,
):
    """Obtiene media y error relativo sin cargar el tally completo en RAM."""

    if num_realizations <= 1:
        raise ValueError(
            "Se requieren al menos dos batches para calcular el error relativo"
        )
    if expected_cells <= 0 or chunk_cells <= 0:
        raise ValueError("La malla y el tamaño de bloque deben ser positivos")

    mean_destination = Path(mean_destination)
    relative_error_destination = Path(relative_error_destination)
    mean_destination.parent.mkdir(parents=True, exist_ok=True)
    relative_error_destination.parent.mkdir(parents=True, exist_ok=True)
    mean_values = np.memmap(
        mean_destination,
        dtype=np.float32,
        mode="w+",
        shape=(expected_cells,),
    )
    relative_error = np.memmap(
        relative_error_destination,
        dtype=np.float32,
        mode="w+",
        shape=(expected_cells,),
    )
    dataset_path = "tallies/tally %s/results" % int(tally_id)
    inverse_realizations = 1.0 / float(num_realizations)
    scale_values = _scale_values(scale, expected_cells)

    with h5py.File(statepoint_file, "r") as statepoint:
        if dataset_path not in statepoint:
            raise KeyError("No existe %s en el statepoint" % dataset_path)
        results = statepoint[dataset_path]
        if results.ndim != 3 or results.shape[2] < 2:
            raise ValueError("El statepoint no contiene suma y suma de cuadrados")
        if results.shape[0] != expected_cells:
            raise ValueError("El número de bins del tally no coincide con la malla")
        if score_index < 0 or score_index >= results.shape[1]:
            raise ValueError("El índice de score no existe en el tally")

        for start in range(0, expected_cells, chunk_cells):
            stop = min(start + chunk_cells, expected_cells)
            tally_sum = np.asarray(
                results[start:stop, score_index, 0],
                dtype=np.float64,
            )
            tally_sum_sq = np.asarray(
                results[start:stop, score_index, 1],
                dtype=np.float64,
            )
            mean = tally_sum * inverse_realizations
            variance_of_mean = (
                tally_sum_sq * inverse_realizations - np.square(mean)
            ) / float(num_realizations - 1)
            np.maximum(variance_of_mean, 0.0, out=variance_of_mean)
            block_error = np.full(mean.shape, np.nan, dtype=np.float64)
            nonzero = mean != 0.0
            block_error[nonzero] = (
                np.sqrt(variance_of_mean[nonzero]) / np.abs(mean[nonzero])
            )
            block_scale = (
                scale_values
                if np.isscalar(scale_values)
                else scale_values[start:stop]
            )
            mean_values[start:stop] = mean * block_scale
            relative_error[start:stop] = block_error

    mean_values.flush()
    relative_error.flush()
    return mean_values, relative_error


def _finite_values(values: np.ndarray, inside_mask: Optional[np.ndarray]) -> np.ndarray:
    valid = np.isfinite(values)
    if inside_mask is not None:
        valid &= inside_mask
    return values[valid]


def summarize_dose(
    values: np.ndarray,
    mesh: RegularMeshSpec,
    inside_mask: Optional[np.ndarray] = None,
) -> dict:
    selected = _finite_values(values, inside_mask)
    if selected.size == 0:
        return {
            "minimum_dose": None,
            "maximum_dose": None,
            "average_dose": None,
            "dose_uniformity_ratio": None,
            "operational_dose_uniformity_ratio": None,
            "total_cells": mesh.cell_count,
            "active_cells": 0,
        }

    minimum = float(np.min(selected))
    maximum = float(np.max(selected))
    percentiles = np.percentile(selected, [2, 5, 10, 50, 90, 95, 98])
    p02, p05, p10, median, p90, p95, p98 = (
        float(value) for value in percentiles
    )
    return {
        "minimum_dose": minimum,
        "maximum_dose": maximum,
        "average_dose": float(np.mean(selected, dtype=np.float64)),
        "dose_uniformity_ratio": maximum / minimum if minimum > 0 else None,
        "dose_p02": p02,
        "dose_p05": p05,
        "dose_p10": p10,
        "dose_median": median,
        "dose_p90": p90,
        "dose_p95": p95,
        "dose_p98": p98,
        "operational_dose_uniformity_ratio": p90 / p10 if p10 > 0 else None,
        "robust_dose_uniformity_ratio": p95 / p05 if p05 > 0 else None,
        "d98_d2_uniformity_ratio": p98 / p02 if p02 > 0 else None,
        "total_cells": mesh.cell_count,
        "active_cells": int(selected.size),
    }


def summarize_relative_error(
    values: np.ndarray,
    inside_mask: Optional[np.ndarray] = None,
) -> dict:
    selected = _finite_values(values, inside_mask)
    if selected.size == 0:
        return {
            "relative_error_median": None,
            "relative_error_p90": None,
            "relative_error_p95": None,
            "relative_error_p98": None,
            "relative_error_maximum": None,
            "cells_relative_error_below_1_percent": 0,
            "cells_relative_error_below_5_percent": 0,
            "cells_relative_error_below_10_percent": 0,
            "cells_with_error_estimate": 0,
        }
    return {
        "relative_error_median": float(np.median(selected)),
        "relative_error_p90": float(np.percentile(selected, 90)),
        "relative_error_p95": float(np.percentile(selected, 95)),
        "relative_error_p98": float(np.percentile(selected, 98)),
        "relative_error_maximum": float(np.max(selected)),
        "cells_relative_error_below_1_percent": int(np.count_nonzero(selected < 0.01)),
        "cells_relative_error_below_5_percent": int(np.count_nonzero(selected <= 0.05)),
        "cells_relative_error_below_10_percent": int(np.count_nonzero(selected <= 0.10)),
        "cells_with_error_estimate": int(selected.size),
    }


def build_or_load_surface_mask(
    surface_mesh_file: os.PathLike,
    mesh: RegularMeshSpec,
    cache_root: os.PathLike,
    target_position: Optional[Sequence[float]] = None,
    length_scale_to_cm: float = 1.0,
    chunk_cells: int = 250_000,
) -> np.ndarray:
    """Calcula por bloques y almacena la máscara de una superficie triangulada.

    La superficie debe provenir de la triangulación del STEP usada por el
    adaptador CAD→OpenMC. La escala es explícita para no confundir esta entrada
    Monte Carlo con el STL independiente del método determinista.
    """

    import trimesh

    source = Path(surface_mesh_file)
    if not source.is_file():
        raise FileNotFoundError(source)
    if chunk_cells <= 0:
        raise ValueError("chunk_cells debe ser positivo")
    if length_scale_to_cm <= 0:
        raise ValueError("length_scale_to_cm debe ser positivo")

    digest = hashlib.sha256()
    with source.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    digest.update(json.dumps(asdict(mesh), sort_keys=True).encode("utf-8"))
    digest.update(json.dumps(list(target_position) if target_position is not None else None).encode("utf-8"))
    digest.update(str(float(length_scale_to_cm)).encode("ascii"))
    cache = Path(cache_root) / ("load-mask-%s.npz" % digest.hexdigest()[:24])
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.is_file():
        with np.load(cache) as stored:
            count = int(stored["count"])
            if count == mesh.cell_count:
                return np.unpackbits(stored["packed"], count=count).astype(bool, copy=False)

    surface = trimesh.load_mesh(source, force="mesh", process=False)
    surface.apply_scale(float(length_scale_to_cm))
    if target_position is not None:
        target = np.asarray(target_position, dtype=np.float64)
        if target.shape != (3,):
            raise ValueError("target_position debe contener X, Y y Z")
        surface.apply_translation(target - surface.vertices.mean(axis=0))

    nx, ny, nz = mesh.dimension
    lower = np.asarray(mesh.lower_left, dtype=np.float64)
    spacing = np.asarray(mesh.spacing, dtype=np.float64)
    inside = np.zeros(mesh.cell_count, dtype=bool)
    surface_bounds = np.asarray(surface.bounds, dtype=np.float64)
    first = np.ceil((surface_bounds[0] - lower) / spacing - 0.5).astype(np.int64)
    last = np.floor((surface_bounds[1] - lower) / spacing - 0.5).astype(np.int64)
    first = np.maximum(first, 0)
    last = np.minimum(last, np.asarray((nx, ny, nz), dtype=np.int64) - 1)
    candidate_shape = np.maximum(last - first + 1, 0)
    candidate_count = int(np.prod(candidate_shape, dtype=np.int64))
    candidate_nx, candidate_ny, _ = candidate_shape
    for start in range(0, candidate_count, chunk_cells):
        stop = min(start + chunk_cells, candidate_count)
        local_ids = np.arange(start, stop, dtype=np.int64)
        i = first[0] + local_ids % candidate_nx
        j = first[1] + (local_ids // candidate_nx) % candidate_ny
        k = first[2] + local_ids // (candidate_nx * candidate_ny)
        ids = i + nx * (j + ny * k)
        indices = np.column_stack(
            (
                i,
                j,
                k,
            )
        )
        centers = lower + (indices + 0.5) * spacing
        inside[ids] = surface.contains(centers)

    temporary = cache.with_suffix(".tmp.npz")
    np.savez_compressed(temporary, packed=np.packbits(inside), count=mesh.cell_count)
    temporary.replace(cache)
    return inside


def build_material_ids(
    material_surface_files,
    mesh: RegularMeshSpec,
    cache_root: os.PathLike,
    length_scale_to_cm: float = 1.0,
    chunk_cells: int = 250_000,
):
    """Clasifica cada centro de celda dentro de un material de la carga.

    El identificador cero se reserva para el exterior. Los materiales reciben
    identificadores estables en orden alfabético para que el visor web pueda
    reconstruir una leyenda categórica sin alterar los cortes de dosis.
    """

    names = sorted(str(name).strip().lower() for name in material_surface_files)
    if len(names) > np.iinfo(np.uint16).max:
        raise ValueError("La cantidad de materiales excede el formato VTI admitido")
    material_ids = np.zeros(mesh.cell_count, dtype=np.uint16)
    labels = []
    for material_id, name in enumerate(names, start=1):
        mask = build_or_load_surface_mask(
            material_surface_files[name],
            mesh,
            Path(cache_root) / name,
            length_scale_to_cm=length_scale_to_cm,
            chunk_cells=chunk_cells,
        )
        overlap = mask & (material_ids != 0)
        if np.any(overlap):
            raise ValueError(
                "Los volúmenes de los materiales STEP se superponen: %s" % name
            )
        material_ids[mask] = material_id
        labels.append({"id": material_id, "name": name})
    return material_ids, labels


def _bounded_contains(surface, points):
    """Bound scratch memory of the native Python ray fallback as well as Embree."""
    batch = 4096 if surface.ray.__class__.__module__.endswith("ray_pyembree") else 128
    inside = np.empty(len(points), dtype=bool)
    for start in range(0, len(points), batch):
        inside[start:start + batch] = surface.contains(points[start:start + batch])
    return inside


def _orthogonal_volume_boxes(surface):
    """Decompose a closed rectilinear solid into exact non-overlapping boxes."""
    normals = np.abs(surface.face_normals)
    if not surface.is_watertight or not np.all(normals.max(axis=1) > 1 - 1e-9):
        return None
    planes = [np.unique(np.round(surface.vertices[:, axis], 8)) for axis in range(3)]
    shape = [len(values) - 1 for values in planes]
    if min(shape) < 1 or math.prod(shape) > 4096:
        return None
    index = np.stack(np.meshgrid(*(np.arange(n) for n in shape), indexing="ij"), axis=-1).reshape(-1, 3)
    low = np.column_stack([planes[axis][index[:, axis]] for axis in range(3)])
    high = np.column_stack([planes[axis][index[:, axis] + 1] for axis in range(3)])
    occupied = _bounded_contains(surface, (low + high) / 2)
    if not occupied.any():
        return None
    # Reject a decomposition that does not reproduce the mesh's own volume.
    if not np.isclose(np.prod(high[occupied] - low[occupied], axis=1).sum(), abs(surface.volume), rtol=1e-6, atol=1e-9):
        return None
    return low[occupied], high[occupied]


def _aggregate_material_volume_fractions(
    material_surface_files,
    mesh: RegularMeshSpec,
    cache_root: os.PathLike,
    length_scale_to_cm: float = 1.0,
    samples_per_axis: int = 3,
    chunk_cells: int = 25_000,
    split_disjoint: bool = False,
):
    """Estima la fracción volumétrica de cada material en celdas de borde.

    Se usa muestreo cartesiano estratificado y determinista. A diferencia de
    clasificar únicamente el centro, una celda cortada por el STEP se divide
    por la masa material realmente ocupada al convertir ``heating`` a dosis.
    """

    import trimesh

    samples_per_axis = int(samples_per_axis)
    if samples_per_axis < 1 or samples_per_axis > 7:
        raise ValueError("samples_per_axis debe estar entre 1 y 7")
    if chunk_cells < 1:
        raise ValueError("chunk_cells debe ser positivo")

    names = sorted(str(name).strip().lower() for name in material_surface_files)
    fractions = np.zeros((len(names), mesh.cell_count), dtype=np.float32)
    labels = [{"id": index + 1, "name": name} for index, name in enumerate(names)]
    lower = np.asarray(mesh.lower_left, dtype=np.float64)
    spacing = np.asarray(mesh.spacing, dtype=np.float64)
    nx, ny, nz = mesh.dimension
    offsets = (np.arange(samples_per_axis, dtype=np.float64) + 0.5) / samples_per_axis
    offsets -= 0.5
    sample_offsets = np.asarray(
        [
            (offset_x, offset_y, offset_z)
            for offset_x in offsets
            for offset_y in offsets
            for offset_z in offsets
        ],
        dtype=np.float64,
    )

    for row, name in enumerate(names):
        source = Path(material_surface_files[name])
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
        digest.update(json.dumps(asdict(mesh), sort_keys=True).encode("utf-8"))
        digest.update(str(float(length_scale_to_cm)).encode("ascii"))
        digest.update(str(samples_per_axis).encode("ascii"))
        digest.update(b"orthogonal-exact-bounded-rays-v2")
        digest.update(str(trimesh.ray.has_embree).encode("ascii"))
        if split_disjoint:
            digest.update(b"disjoint-v1")
        cache = Path(cache_root) / name / ("volume-fraction-%s.npz" % digest.hexdigest()[:24])
        cache.parent.mkdir(parents=True, exist_ok=True)
        if cache.is_file():
            with np.load(cache) as stored:
                cached = np.asarray(stored["fractions"], dtype=np.float32)
            if cached.size == mesh.cell_count:
                fractions[row] = cached
                continue

        surface = trimesh.load_mesh(source, force="mesh", process=False)
        surface.apply_scale(float(length_scale_to_cm))
        surfaces = [surface]
        if split_disjoint:
            # A pallet often has many closed solids with the same material.
            # Independent bounds keep ray queries local and cap scratch memory.
            welded = surface.copy()
            welded.merge_vertices()
            welded.update_faces(welded.nondegenerate_faces())
            parts = welded.split(only_watertight=False)
            if 0 < len(parts) <= 100 and all(part.is_watertight for part in parts):
                bounds = [part.bounds for part in parts]
                if all(np.any(a[1] < b[0]) or np.any(b[1] < a[0])
                       for i, a in enumerate(bounds) for b in bounds[i + 1:]):
                    surfaces = parts
        block_cells = min(chunk_cells, 1000) if split_disjoint else chunk_cells
        material_fraction = np.zeros(mesh.cell_count, dtype=np.float32)
        sample_count = float(samples_per_axis ** 3)
        half_cell_diagonal = float(np.linalg.norm(spacing) * 0.5)

        for component in surfaces:
            orthogonal = _orthogonal_volume_boxes(component)
            surface_bounds = np.asarray(component.bounds, dtype=np.float64)
            first = np.floor((surface_bounds[0] - lower) / spacing).astype(np.int64)
            last = np.floor((surface_bounds[1] - lower) / spacing).astype(np.int64)
            first = np.maximum(first, 0)
            last = np.minimum(last, np.asarray((nx, ny, nz), dtype=np.int64) - 1)
            candidate_shape = np.maximum(last - first + 1, 0)
            candidate_count = int(np.prod(candidate_shape, dtype=np.int64))
            candidate_nx, candidate_ny, _ = candidate_shape
            for start in range(0, candidate_count, block_cells):
                stop = min(start + block_cells, candidate_count)
                local_ids = np.arange(start, stop, dtype=np.int64)
                i = first[0] + local_ids % candidate_nx
                j = first[1] + (local_ids // candidate_nx) % candidate_ny
                k = first[2] + local_ids // (candidate_nx * candidate_ny)
                flat_ids = i + nx * (j + ny * k)
                indices = np.column_stack((i, j, k)).astype(np.float64, copy=False)
                if orthogonal is not None:
                    cell_low = lower + indices * spacing
                    cell_high = cell_low + spacing
                    overlap = np.maximum(0, np.minimum(cell_high[:, None, :], orthogonal[1])
                                         - np.maximum(cell_low[:, None, :], orthogonal[0]))
                    material_fraction[flat_ids] += (np.prod(overlap, axis=2).sum(axis=1) / np.prod(spacing)).astype(np.float32)
                    continue
                centers = lower + (indices + 0.5) * spacing
                center_inside = _bounded_contains(component, centers)
                # Only cells close enough to a CAD face can have a fractional
                # occupancy.  Interior/exterior cells are resolved from one centre
                # query; the 3^3 stratified samples are reserved for boundary
                # cells.  This is especially important for one-centimetre meshes,
                # whose volume grows cubically while their boundary grows roughly
                # quadratically.
                distance = np.empty(len(centers))
                for block_start in range(0, len(centers), 64):
                    distance[block_start:block_start + 64] = component.nearest.on_surface(centers[block_start:block_start + 64])[1]
                boundary = distance <= half_cell_diagonal * (1.0 + 1.0e-9)
                block_fraction = center_inside.astype(np.float32)
                if np.any(boundary):
                    boundary_indices = indices[boundary]
                    points = lower + (
                        boundary_indices[:, np.newaxis, :]
                        + 0.5
                        + sample_offsets[np.newaxis, :, :]
                    ) * spacing
                    occupied = _bounded_contains(component, points.reshape(-1, 3)).reshape(
                        boundary_indices.shape[0], sample_offsets.shape[0]
                    ).sum(axis=1, dtype=np.uint16)
                    block_fraction[boundary] = occupied.astype(np.float32) / sample_count
                material_fraction[flat_ids] += block_fraction

        with TemporaryDirectory(dir=cache.parent) as staging:
            temporary = Path(staging) / cache.name
            np.savez_compressed(temporary, fractions=material_fraction)
            temporary.replace(cache)
        fractions[row] = material_fraction

    total_fraction = np.sum(fractions, axis=0, dtype=np.float32)
    if np.any(total_fraction > 1.0 + 1.0 / (samples_per_axis ** 3)):
        raise ValueError("Los volúmenes de los materiales STEP se superponen")
    return fractions, labels


def build_material_volume_fractions(
    material_surface_files,
    mesh: RegularMeshSpec,
    cache_root: os.PathLike,
    length_scale_to_cm: float = 1.0,
    samples_per_axis: int = 3,
    chunk_cells: int = 25_000,
    split_disjoint: bool = False,
    *,
    component_surface_files=None,
    component_length_scale_to_cm=None,
    component_translation_cm=(0., 0., 0.),
    cpu_threads=1,
    memory_limit_mb=4096,
    progress_callback=None,
):
    """Fracciones materiales conservando el CAD y el muestreo estratificado.

    Los cuerpos CAD explícitos conservan sus cavidades y permiten limitar las
    consultas a cada envase. La caché guarda cuerpos y bloques completos para
    poder continuar una preparación interrumpida.
    """
    if split_disjoint and component_surface_files is None:
        # Existing callers supplying only grouped meshes (e.g. PHITS) keep
        # their existing decomposition and cache. OpenMC has the STEP bodies.
        return _aggregate_material_volume_fractions(
            material_surface_files, mesh, cache_root, length_scale_to_cm,
            samples_per_axis, chunk_cells, split_disjoint=True,
        )
    from .material_occupancy import material_volume_fractions
    return material_volume_fractions(
        material_surface_files, mesh, cache_root,
        length_scale_to_cm=length_scale_to_cm, samples_per_axis=samples_per_axis,
        chunk_cells=chunk_cells, component_surface_files=component_surface_files,
        component_length_scale_to_cm=component_length_scale_to_cm,
        component_translation_cm=component_translation_cm, cpu_threads=cpu_threads,
        memory_limit_mb=memory_limit_mb, progress_callback=progress_callback,
    )


def write_compressed_vti(
    values: np.ndarray,
    mesh: RegularMeshSpec,
    destination: os.PathLike,
    inside_mask: Optional[np.ndarray] = None,
    material_ids: Optional[np.ndarray] = None,
    relative_error: Optional[np.ndarray] = None,
    scalar_name: str = "absorbed_dose_rate_kgy_h",
    absolute_std_dev: Optional[np.ndarray] = None,
) -> Path:
    """Escribe todas las celdas como VTK ImageData binario comprimido."""

    import vtk
    from vtk.util.numpy_support import numpy_to_vtk

    flat_values = np.asarray(values, dtype=np.float32).reshape(-1)
    if flat_values.size != mesh.cell_count:
        raise ValueError("La cantidad de valores no coincide con la malla")
    if inside_mask is not None:
        inside_mask = np.asarray(inside_mask, dtype=bool).reshape(-1)
        if inside_mask.size != mesh.cell_count:
            raise ValueError("La máscara no coincide con la malla")
    if material_ids is not None:
        material_ids = np.asarray(material_ids, dtype=np.uint16).reshape(-1)
        if material_ids.size != mesh.cell_count:
            raise ValueError("Los identificadores de material no coinciden con la malla")
    if relative_error is not None:
        relative_error = np.asarray(relative_error, dtype=np.float32).reshape(-1)
        if relative_error.size != mesh.cell_count:
            raise ValueError("El error relativo no coincide con la malla")
    if absolute_std_dev is not None:
        absolute_std_dev = np.asarray(absolute_std_dev, dtype=np.float32).reshape(-1)
        if absolute_std_dev.size != mesh.cell_count:
            raise ValueError("La desviación absoluta no coincide con la malla")

    destination = Path(destination).with_suffix(".vti")
    destination.parent.mkdir(parents=True, exist_ok=True)
    image = vtk.vtkImageData()
    image.SetDimensions(*(value + 1 for value in mesh.dimension))
    image.SetOrigin(*mesh.lower_left)
    image.SetSpacing(*mesh.spacing)

    vtk_values = numpy_to_vtk(flat_values, deep=False, array_type=vtk.VTK_FLOAT)
    vtk_values.SetName(scalar_name)
    image.GetCellData().SetScalars(vtk_values)
    if absolute_std_dev is not None:
        vtk_absolute = numpy_to_vtk(absolute_std_dev, deep=True, array_type=vtk.VTK_FLOAT)
        vtk_absolute.SetName("absolute_std_dev")
        image.GetCellData().AddArray(vtk_absolute)

    vtk_ghosts = None
    if inside_mask is not None:
        ghosts = np.zeros(mesh.cell_count, dtype=np.uint8)
        ghosts[~inside_mask] = vtk.vtkDataSetAttributes.HIDDENCELL
        vtk_ghosts = numpy_to_vtk(ghosts, deep=True, array_type=vtk.VTK_UNSIGNED_CHAR)
        vtk_ghosts.SetName(vtk.vtkDataSetAttributes.GhostArrayName())
        image.GetCellData().AddArray(vtk_ghosts)

    vtk_material_ids = None
    if material_ids is not None:
        vtk_material_ids = numpy_to_vtk(
            material_ids,
            deep=True,
            array_type=vtk.VTK_UNSIGNED_SHORT,
        )
        vtk_material_ids.SetName("material_id")
        image.GetCellData().AddArray(vtk_material_ids)

    vtk_relative_error = None
    if relative_error is not None:
        vtk_relative_error = numpy_to_vtk(
            relative_error,
            deep=False,
            array_type=vtk.VTK_FLOAT,
        )
        vtk_relative_error.SetName("relative_error")
        image.GetCellData().AddArray(vtk_relative_error)

    writer = vtk.vtkXMLImageDataWriter()
    writer.SetFileName(str(destination))
    writer.SetInputData(image)
    writer.SetDataModeToAppended()
    writer.EncodeAppendedDataOff()
    writer.SetCompressorTypeToZLib()
    compressor = writer.GetCompressor()
    if compressor is not None and hasattr(compressor, "SetCompressionLevel"):
        compressor.SetCompressionLevel(6)
    if writer.Write() != 1:
        raise OSError("VTK no pudo escribir %s" % destination)
    return destination


def _plot_cut(
    data: np.ndarray,
    x_extent: Sequence[float],
    y_extent: Sequence[float],
    x_label: str,
    y_label: str,
    title: str,
    destination: Path,
    quantity_label: str = "Tasa de dosis absorbida [kGy/h]",
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    finite = data[np.isfinite(data)]
    figure, axis = plt.subplots(figsize=(8.4, 6.2), constrained_layout=True)
    if finite.size:
        image = axis.imshow(
            data.T,
            origin="lower",
            extent=(x_extent[0], x_extent[1], y_extent[0], y_extent[1]),
            interpolation="nearest",
            aspect="equal",
            cmap="viridis",
        )
        colorbar = figure.colorbar(image, ax=axis)
        colorbar.set_label(quantity_label)
    else:
        axis.text(0.5, 0.5, "Sin celdas activas", ha="center", va="center", transform=axis.transAxes)
    axis.set_xlabel(x_label)
    axis.set_ylabel(y_label)
    axis.set_title(title)
    figure.savefig(destination, dpi=170)
    plt.close(figure)


def write_isodose_cuts(
    values: np.ndarray,
    mesh: RegularMeshSpec,
    output_root: os.PathLike,
    inside_mask: Optional[np.ndarray] = None,
    quantity_label: str = "Tasa de dosis absorbida [kGy/h]",
) -> dict:
    """Genera cortes centrales XY y YZ directamente, sin tablas intermedias."""

    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    volume = np.asarray(values).reshape(mesh.dimension, order="F")
    mask_volume = None
    if inside_mask is not None:
        mask_volume = np.asarray(inside_mask, dtype=bool).reshape(mesh.dimension, order="F")

    nx, ny, nz = mesh.dimension
    xy = np.asarray(volume[:, :, nz // 2], dtype=np.float32)
    yz = np.asarray(volume[nx // 2, :, :], dtype=np.float32)
    if mask_volume is not None:
        xy = np.where(mask_volume[:, :, nz // 2], xy, np.nan)
        yz = np.where(mask_volume[nx // 2, :, :], yz, np.nan)

    xy_path = root / "isodose_xy.png"
    yz_path = root / "isodose_yz.png"
    _plot_cut(
        xy,
        (mesh.lower_left[0], mesh.upper_right[0]),
        (mesh.lower_left[1], mesh.upper_right[1]),
        "X [cm]",
        "Y [cm]",
        "Monte Carlo · corte XY",
        xy_path,
        quantity_label,
    )
    _plot_cut(
        yz,
        (mesh.lower_left[1], mesh.upper_right[1]),
        (mesh.lower_left[2], mesh.upper_right[2]),
        "Y [cm]",
        "Z [cm]",
        "Monte Carlo · corte YZ",
        yz_path,
        quantity_label,
    )
    return {"cut_xy": xy_path.name, "cut_yz": yz_path.name}


def write_structured_dose_artifacts(
    values: np.ndarray,
    mesh: RegularMeshSpec,
    output_root: os.PathLike,
    inside_mask: Optional[np.ndarray] = None,
    material_ids: Optional[np.ndarray] = None,
    material_labels=None,
    relative_error: Optional[np.ndarray] = None,
    vtk_filename: str = "dose_distribution.vti",
    absolute_std_dev: Optional[np.ndarray] = None,
) -> dict:
    """Escribe VTI, cortes y metadatos conservando todas las celdas."""

    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    vtk_path = write_compressed_vti(
        values,
        mesh,
        root / vtk_filename,
        inside_mask=inside_mask,
        material_ids=material_ids,
        relative_error=relative_error,
        absolute_std_dev=absolute_std_dev,
    )
    cuts = write_isodose_cuts(values, mesh, root, inside_mask=inside_mask)
    visualization = summarize_dose(values, mesh, inside_mask=inside_mask)
    if relative_error is not None:
        visualization.update(
            summarize_relative_error(relative_error, inside_mask=inside_mask)
        )
    visualization.update(
        {
            "vtk": vtk_path.name,
            "vtk_format": "vti",
            "mesh": asdict(mesh),
            **cuts,
        }
    )
    if material_labels is not None:
        visualization["materials"] = list(material_labels)
    with (root / "visualization.json").open("w", encoding="utf-8") as stream:
        json.dump(visualization, stream, ensure_ascii=False, indent=2)
    return visualization


def _statepoint_metadata(statepoint_file: os.PathLike, tally_name: str):
    """Lee solo metadatos HDF5 y evita reconstruir ``MeshFilter.bins``."""

    with h5py.File(statepoint_file, "r") as statepoint:
        tallies = statepoint["tallies"]
        selected = None
        for group_name, group in tallies.items():
            if not group_name.startswith("tally "):
                continue
            name = group["name"][()].decode() if "name" in group else ""
            if name == tally_name:
                selected = group
                break
        if selected is None:
            raise LookupError("No se encontró el tally %s" % tally_name)

        tally_id = int(selected.name.rsplit("tally ", 1)[1])
        num_realizations = int(selected["n_realizations"][()])
        filter_ids = np.atleast_1d(selected["filters"][()])
        mesh_ids = []
        for filter_id in filter_ids:
            filter_group = tallies["filters/filter %s" % int(filter_id)]
            if filter_group["type"][()].decode() == "mesh":
                mesh_ids.append(int(np.asarray(filter_group["bins"][()]).reshape(-1)[0]))
        if len(mesh_ids) != 1:
            raise ValueError("El tally debe contener exactamente un filtro de malla")

        mesh_group = tallies["meshes/mesh %s" % mesh_ids[0]]
        if mesh_group["type"][()].decode() != "regular":
            raise TypeError("El posprocesador optimizado requiere openmc.RegularMesh")
        dimension = tuple(int(value) for value in mesh_group["dimension"][()])
        lower_left = tuple(float(value) for value in mesh_group["lower_left"][()])
        if "upper_right" in mesh_group:
            upper_right = tuple(float(value) for value in mesh_group["upper_right"][()])
        elif "width" in mesh_group:
            width = mesh_group["width"][()]
            upper_right = tuple(
                low + float(step) * cells
                for low, step, cells in zip(lower_left, width, dimension)
            )
        else:
            raise ValueError("El statepoint no contiene los límites de la malla")
        return tally_id, num_realizations, RegularMeshSpec(
            dimension=dimension,
            lower_left=lower_left,
            upper_right=upper_right,
        )


def process_statepoint_to_artifacts(
    statepoint_file: os.PathLike,
    output_root: os.PathLike,
    dose_coeff: float,
    tally_name: str = "Flux",
    inside_mask: Optional[np.ndarray] = None,
    material_ids: Optional[np.ndarray] = None,
    material_labels=None,
    chunk_cells: int = 1_000_000,
    vtk_filename: str = "dose_distribution.vti",
    dose_scale=None,
    additional_relative_error=None,
) -> dict:
    """Ruta completa statepoint → VTI/cortes, sin CSV ni Delaunay."""

    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    tally_id, num_realizations, mesh = _statepoint_metadata(statepoint_file, tally_name)
    temporary = root / ".dose_distribution.float32.tmp"
    relative_error_temporary = root / ".relative_error.float32.tmp"
    dose, relative_error = read_tally_statistics_chunked(
        statepoint_file,
        tally_id,
        num_realizations,
        mesh.cell_count,
        mean_destination=temporary,
        relative_error_destination=relative_error_temporary,
        scale=(
            float(dose_coeff) / mesh.cell_volume
            if dose_scale is None
            else dose_scale
        ),
        chunk_cells=chunk_cells,
    )
    if additional_relative_error is not None:
        extra = _scale_values(additional_relative_error, mesh.cell_count)
        np.hypot(relative_error, extra, out=relative_error)
        relative_error.flush()
    try:
        result = write_structured_dose_artifacts(
            dose,
            mesh,
            root,
            inside_mask=inside_mask,
            material_ids=material_ids,
            material_labels=material_labels,
            relative_error=relative_error,
            vtk_filename=vtk_filename,
        )
        result.update(
            {
                "tally_name": tally_name,
                "tally_id": int(tally_id),
                "num_realizations": int(num_realizations),
                "dose_coefficient": (
                    float(dose_coeff) if dose_scale is None else None
                ),
                "postprocess": "statepoint-chunked-vti",
            }
        )
        result["visualization"] = {
            key: value
            for key, value in result.items()
            if key not in {"visualization"}
        }
        with (root / "result.json").open("w", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        return result
    finally:
        dose.flush()
        relative_error.flush()
        del dose
        del relative_error
        temporary.unlink(missing_ok=True)
        relative_error_temporary.unlink(missing_ok=True)


def summarize_statepoint_tally(
    statepoint_file: os.PathLike,
    output_root: os.PathLike,
    dose_coeff: float,
    tally_name: str,
    inside_mask: Optional[np.ndarray] = None,
    chunk_cells: int = 1_000_000,
    dose_scale=None,
    additional_relative_error=None,
) -> dict:
    """Resume un tally de evaluación sin generar un segundo VTK.

    Esta ruta permite puntuar una malla espacial más gruesa durante el mismo
    transporte. Sus estadísticas no son un filtro aplicado al campo fino: son
    estimadores Monte Carlo diferenciados, acumulados directamente por OpenMC
    sobre volúmenes mayores durante las mismas historias.
    """

    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    tally_id, num_realizations, mesh = _statepoint_metadata(statepoint_file, tally_name)
    safe_name = "".join(
        character.lower() if character.isalnum() else "-"
        for character in tally_name
    ).strip("-") or "tally"
    mean_temporary = root / (".%s-mean.float32.tmp" % safe_name)
    error_temporary = root / (".%s-relative-error.float32.tmp" % safe_name)
    mean_values, relative_error = read_tally_statistics_chunked(
        statepoint_file,
        tally_id,
        num_realizations,
        mesh.cell_count,
        mean_destination=mean_temporary,
        relative_error_destination=error_temporary,
        scale=(
            float(dose_coeff) / mesh.cell_volume
            if dose_scale is None
            else dose_scale
        ),
        chunk_cells=chunk_cells,
    )
    if additional_relative_error is not None:
        extra = _scale_values(additional_relative_error, mesh.cell_count)
        np.hypot(relative_error, extra, out=relative_error)
        relative_error.flush()
    try:
        result = summarize_dose(mean_values, mesh, inside_mask=inside_mask)
        result.update(summarize_relative_error(relative_error, inside_mask=inside_mask))
        result.update(
            {
                "tally_name": tally_name,
                "tally_id": int(tally_id),
                "num_realizations": int(num_realizations),
                "mesh": asdict(mesh),
                "cell_size_cm": list(mesh.spacing),
                "method": "direct-coarse-mesh-tally",
            }
        )
        return result
    finally:
        mean_values.flush()
        relative_error.flush()
        del mean_values
        del relative_error
        mean_temporary.unlink(missing_ok=True)
        error_temporary.unlink(missing_ok=True)
