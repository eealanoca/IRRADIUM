from __future__ import annotations

import json
import fcntl
import hashlib
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from sim_gamma.compute_resources import resolve_cpu_threads

from .cad_workflow import prepare_phits_from_step
from .model import PHITS_DEFAULT_ROOT
from .postprocess import combine_phits_vtk, extreme_summary
from .runner import completed_history_layout, has_fatal_output, resolve_runtime, run_input


def _write_json(path: Path, document) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, ensure_ascii=False, indent=2, allow_nan=False)
    temporary.replace(path)


def _status(path: Path, request, stage: str, progress: int, **extra) -> None:
    if "route_progress" in request:
        from sim_gamma.workers.monte_carlo_cli import write_status
        return write_status(path, request, stage, progress, dose_tally="phits", **extra)
    _write_json(
        path,
        {
            "case_id": request["case_id"],
            "run_id": request["run_id"],
            "state": "running",
            "stage": stage,
            "progress": progress,
            "execution_enabled": True,
            "dose_tally": "phits",
            **extra,
        },
    )


def _geometry_check_signature(model, runtime):
    """Key only checks that are independent of the emitting source pose."""
    source = model.input_path.read_text(encoding="utf-8")
    sections = re.split(r"(?m)^\[\s*([^\]]+)\s*\]\s*$", source)
    by_name = {sections[i].strip().lower(): sections[i + 1] for i in range(1, len(sections), 2)}
    if not all(name in by_name for name in ("material", "surface", "cell", "t-deposit")):
        return None
    surfaces = by_name["surface"]
    boxes = {int(match[1]): np.asarray([float(x) for x in match[2].split()]).reshape(3, 2)
             for match in re.finditer(r"(?m)^\s*(10|11|20) rpp ([^\n]+)$", surfaces)}
    # The outer air box may move with the source without affecting any
    # geometry inside the diagnostic/load domain. Normalize it only after
    # confirming strict containment of both inner boxes.
    if all(number in boxes for number in (10, 11, 20)) and all(
        np.all(boxes[20][:, 0] < boxes[number][:, 0])
        and np.all(boxes[20][:, 1] > boxes[number][:, 1]) for number in (10, 11)
    ):
        surfaces = re.sub(r"(?m)^\s*20 rpp [^\n]+$", "20 enclosing-air-box", surfaces)
    geometry = model.geometry_check_path or model.geometry_path
    binary = runtime.executable
    bin_root = Path(runtime.command_prefix[-1]).resolve().parent
    digest = hashlib.sha256()
    with geometry.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    structural_sections = {name: body for name, body in by_name.items()
                           if not name.startswith("t-") and name not in
                           ("title", "source", "parameters", "importance", "weight window", "end")}
    structural_sections["surface"] = surfaces
    parameters = re.sub(
        r"(?m)^\s*(?:icntl|maxcas|maxbch|rseed|itgchk|file\(6\))\s*=.*$", "",
        by_name.get("parameters", ""),
    )
    parameters = "\n".join(line.strip() for line in parameters.splitlines() if line.strip())
    digest.update(json.dumps({
        "version": "full-tetra-gshow24-v2", "geometry_sections": structural_sections,
        "parameters": parameters,
        "tally": re.sub(r"(?m)^\s*file = .*", "", by_name["t-deposit"]),
        "binaries": [[str(path.resolve()), path.stat().st_size, path.stat().st_mtime_ns]
                     for path in sorted({binary, *bin_root.glob("phits*.exe"), bin_root / "phits.sh"})
                     if path.is_file()],
    }, sort_keys=True).encode())
    return digest.hexdigest()


def _geometry_check(model, runtime) -> bool:
    cache_base = getattr(model, "material_fraction_cache", None)
    signature = _geometry_check_signature(model, runtime) if cache_base is not None else None
    if signature is None:
        _run_geometry_check(model, runtime)
        return False
    cache = cache_base.parent / "phits-geometry-checks"
    cache.mkdir(parents=True, exist_ok=True)
    marker = cache / f"{signature}.json"
    with (cache / f"{signature}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            record = json.loads(marker.read_text())
            reused = record.get("key") == signature and record.get("full_tetra_intersection_check") is True
        except (OSError, ValueError, AttributeError):
            reused = False
        if not reused:
            _run_geometry_check(model, runtime)
            record = {"key": signature, "validated_at": datetime.now(timezone.utc).isoformat(),
                      "full_tetra_intersection_check": True, "gshow_max_axis": 24}
            _write_json(marker, record)
        _write_json(model.input_path.parent / "geometry-validation.json",
                    {**record, "reused": reused})
    return reused


def _run_geometry_check(model, runtime) -> None:
    work_root = model.input_path.parent
    phits_root = Path(runtime.command_prefix[-1]).resolve().parent.parent
    check_runtime = resolve_runtime(phits_root, runtime.workers, prefer_openmp=True)
    for stale in work_root.glob("*_geo.out"):
        stale.unlink(missing_ok=True)
    source = model.input_path.read_text(encoding="utf-8")
    if model.geometry_check_path is not None:
        source = source.replace(f"nfile={model.geometry_path.stem}",
                                f"nfile={model.geometry_check_path.stem}")
    # Geometry probes must not repeat the full production budget.
    source = re.sub(r"(?m)^  maxcas = .*", "  maxcas = 100", source)
    source = re.sub(r"(?m)^  maxbch = .*", "  maxbch = 2", source)
    tetra = source.replace("  itgchk = 0", "  itgchk = 1", 1)
    tetra_path = work_root / "sim_gamma_tetra_check.inp"
    tetra_path.write_text(tetra, encoding="utf-8")
    run_input(check_runtime, tetra_path)
    check = source.replace("  icntl = 0", "  icntl = 8", 1)
    check = check.replace("  vtkout = 0", "  vtkout = 1", 1)
    check = re.sub(r"(?m)^  file = deposit-\d+\.out$", "  file = geometry-check.out", check)
    check = check.replace("  epsout = 0", "  gshow = 2\n  epsout = 0", 1)
    # Gshow is a structural diagnostic, not the dose tally. Its cost must not
    # scale with the production voxel resolution. The full tetrahedral
    # intersection check above remains mandatory.
    check = re.sub(r"(?m)^  n([xyz]) = (\d+)$",
                   lambda match: f"  n{match[1]} = {min(24, int(match[2]))}", check)
    # Gshow traces rays at the tally slice centers. A plane tangent to a
    # curved CAD face can make its distance undefined. Offset only the
    # diagnostic sampling planes by 0.1 micrometre. Production geometry,
    # sources and scoring grids remain in their original coordinates.
    for axis in "xyz":
        for edge in ("min", "max"):
            pattern = rf"(?m)^  {axis}{edge} = (.*)$"
            check = re.sub(pattern, lambda match: f"  {axis}{edge} = {float(match[1]) + 1e-5:.12g}", check)
    check_path = work_root / "sim_gamma_geometry_check.inp"
    check_path.write_text(check, encoding="utf-8")
    run_input(check_runtime, check_path)
    geometry_errors = list(work_root.glob("*_geo.out"))
    if geometry_errors:
        excerpt = geometry_errors[0].read_text(encoding="utf-8", errors="replace")[-4000:]
        raise ValueError("PHITS detectó un error geométrico:\n" + excerpt)


def _train_weight_windows(runtime, input_path, mesh, particles):
    work = input_path.parent
    output = work / "route-wwg.out"
    output.unlink(missing_ok=True)
    source = input_path.read_text(encoding="utf-8")
    pilot = re.sub(r"(?m)^  maxcas = .*", f"  maxcas = {max(100, min(100000, particles // 100))}", source)
    pilot = re.sub(r"(?m)^  maxbch = .*", "  maxbch = 10", pilot)
    pilot = re.sub(r"(?m)^  rseed = (\d+)", lambda m: f"  rseed = {int(m[1]) + 1000000}", pilot)
    pilot = re.sub(r"(?m)^  file\(6\) = .*", "  file(6) = route-wwg-summary.out", pilot)
    lines = ["[ T-WWG ]", "  mesh = xyz"]
    for axis, low, high, count in zip("xyz", mesh["lower_left"], mesh["upper_right"], mesh["dimension"], strict=True):
        lines.extend([f"  {axis}-type = 2", f"  {axis}min = {low:.12g}",
                      f"  {axis}max = {high:.12g}", f"  n{axis} = {count}"])
    lines.extend(["  part = photon", "  e-type = 1", "  ne = 1", "  0.0 2.0",
                  "  axis = wwg", f"  file = {output.name}"])
    training_path = work / "route-wwg.inp"
    training_path.write_text(pilot.split("[ T-Deposit ]", 1)[0] + "\n".join(lines) + "\n[ End ]\n")
    run_input(runtime, training_path)
    if not output.is_file() or not re.search(r"\[\s*weight\s+window\s*\]", output.read_text(errors="replace"), re.I):
        raise ValueError("PHITS no generó las ventanas de peso solicitadas")
    input_path.write_text(source.replace("[ Source ]", f"infl: {{{output.name}}}\n\n[ Source ]", 1))


def _recover_completed_transport(model, output_root, position_index):
    """Reuse a completed transport from this run when only publication was interrupted."""
    from .postprocess import read_deposit_pair

    work = model.input_path.parent
    key = (work / "load.bdf.json").read_bytes()
    prefix = f"{position_index + 1:03d}"
    candidates = [path for path in output_root.parent.iterdir()
                  if path.name == prefix or path.name.startswith(prefix + "-")]
    for candidate in sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True):
        if candidate == output_root:
            continue
        previous = candidate / "work" / "phits"
        recovered = []
        try:
            if (previous / "load.bdf.json").read_bytes() != key:
                continue
            for path in model.input_paths:
                if (previous / path.name).read_bytes() != path.read_bytes():
                    break
                index = path.stem.rsplit("_", 1)[-1]
                report = previous / f"phits-{index}.out"
                log = previous / f"{path.stem}.log"
                if not log.is_file() or report.stat().st_mtime_ns > log.stat().st_mtime_ns:
                    break
                if has_fatal_output(log.read_text(errors="replace")):
                    break
                layout = completed_history_layout(report.read_text(errors="replace"))
                if layout is None:
                    break
                numeric = re.search(r"(?im)^\s*vtkout\s*=\s*0\b", path.read_text()) is not None
                suffix = "vtk" if not numeric and (previous / f"deposit-{index}.vtk").is_file() else "out"
                tally = previous / f"deposit-{index}.{suffix}"
                errors = previous / f"deposit-{index}_err.{suffix}"
                if not errors.is_file():
                    break
                read_deposit_pair(previous, index, model.mesh, numeric=numeric)
                recovered.append((layout, (report, log, tally, errors)))
        except (OSError, RuntimeError, ValueError):
            continue
        if len(recovered) == len(model.input_paths):
            for _, paths in recovered:
                for source in paths:
                    shutil.copy2(source, work / source.name)
            print(f"PHITS: transporte terminado recuperado de {candidate.name}.", flush=True)
            return [layout for layout, _ in recovered]
    return []


def run(request_path: Path, *, route_context=None) -> None:
    started = time.perf_counter()
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    case_root = Path(request["case_root"])
    output_root = case_root / "monte_carlo"
    if route_context is not None:
        index, count, pose, _, route = route_context
        request["route_progress"] = (output_root / "status.json", index, count, pose["name"])
        from sim_gamma.domain.study_recovery import position_output_name
        output_root = output_root / "positions" / request["run_id"] / position_output_name(index, route)
        output_root.mkdir(parents=True, exist_ok=True)
    status_path = output_root / "status.json"
    worker_path = output_root / "worker.json"
    simulation = json.loads(
        (case_root / "input" / "simulation_parameters.json").read_text(encoding="utf-8")
    )
    if route_context is not None:
        from sim_gamma.domain.route_study import study_simulation

        simulation = study_simulation(simulation, route_context[-1])
    monte_carlo = simulation.get("monte_carlo", {})
    compute = simulation.get("compute", {})
    particles = int(monte_carlo.get("particles", 1_000_000))
    requested_batches = int(monte_carlo.get("batches", 20))
    cpu_cores = int(compute.get("cpu_cores", -1))
    workers = resolve_cpu_threads(cpu_cores)
    _status(
        status_path,
        request,
        "converting-step-to-phits-geometry",
        5,
        particles=particles,
        cpu_cores=cpu_cores,
        cpu_threads_used=workers,
    )
    _write_json(
        worker_path,
        {
            "case_id": request["case_id"],
            "run_id": request["run_id"],
            "pid": os.getpid(),
            "process_group_id": os.getpgrp(),
            "request_path": str(Path(request_path).resolve()),
            "backend": "phits",
            "started_at": datetime.now(timezone.utc).isoformat(),
            **(
                {"systemd_unit": request["systemd_unit"]}
                if request.get("systemd_unit")
                else {}
            ),
        },
    )
    cad_workflow = prepare_phits_from_step(case_root, cpu_workers=workers, route_context=route_context, output_root=output_root)
    model = cad_workflow.model
    preparation_seconds = time.perf_counter() - started
    _status(
        status_path,
        request,
        "checking-phits-runtime-and-geometry",
        18,
        particles=particles,
        cpu_threads_used=workers,
        source_count=model.source_count,
        input_parts=len(model.input_paths),
    )
    phits_root = Path(os.environ.get("PHITSPATH", PHITS_DEFAULT_ROOT)).resolve()
    # MPI replicates the tetrahedral geometry in every process. Prefer
    # shared memory when those copies alone could consume most of the
    # study budget, keeping the requested CPU threads with OpenMP.
    memory_mb = float((request.get("route_study") or {}).get("memory_limit_mb") or 4096)
    geometry_mb = model.geometry_path.stat().st_size / 1024 ** 2
    shared_geometry = geometry_mb * max(1, workers) * 3 > memory_mb * .5
    runtime = resolve_runtime(phits_root, workers, prefer_openmp=shared_geometry)
    if shared_geometry:
        print(f"PHITS: geometría grande ({geometry_mb:.1f} MiB); "
              f"OpenMP comparte la geometría con {runtime.workers} hilos.", flush=True)
    geometry_check_started = time.perf_counter()
    geometry_check_reused = _geometry_check(model, runtime)
    geometry_check_seconds = time.perf_counter() - geometry_check_started

    transport_started = time.perf_counter()
    if model.variance_reduction.get("method") == "phits_windows":
        _status(status_path, request, "training-phits-photon-weight-windows", 19)
        _train_weight_windows(runtime, model.input_path, model.variance_reduction["guide_mesh"], particles)
    recovered_layouts = (
        _recover_completed_transport(model, output_root, route_context[0])
        if route_context is not None and request.get("resume")
        and model.variance_reduction.get("method") != "phits_windows" else []
    )
    actual_chunk_batch_layouts = list(recovered_layouts)
    pending_inputs = () if recovered_layouts else model.input_paths
    for index, input_path in enumerate(pending_inputs, start=1):
        progress = 20 + int(65 * (index - 1) / len(model.input_paths))
        _status(
            status_path,
            request,
            f"running-phits-t-deposit-{index}-of-{len(model.input_paths)}",
            progress,
            particles=particles,
            cpu_threads_used=runtime.workers,
            parallel_mode=runtime.parallel_mode,
        )
        actual_layout = run_input(runtime, input_path)
        if actual_layout is None:
            raise RuntimeError(f"PHITS no reportó las historias de {input_path.name}")
        actual_chunk_batch_layouts.append(actual_layout)
    transport_seconds = time.perf_counter() - transport_started
    actual_particles = sum(histories * batches for histories, batches in actual_chunk_batch_layouts)
    effective_batches_per_chunk = [
        int(batches) for _, batches in actual_chunk_batch_layouts
    ]
    # El parámetro se aplica a cada bloque de fuentes. Sumar los batches de
    # bloques distintos daría la impresión incorrecta de que cada contribución
    # a la dosis tuvo ese número total de realizaciones.
    effective_batches = min(effective_batches_per_chunk)
    total_batch_realizations = sum(effective_batches_per_chunk)

    def report_material_progress(details):
        _status(status_path, request, "preparing-material-occupancy", 88,
                preparation={**details, "grid": "dose"})

    _status(status_path, request, "processing-phits-dose-statistics", 88)
    values, relative_error, material_ids, inside = combine_phits_vtk(
        model, cpu_threads=workers,
        memory_limit_mb=float(os.environ.get("IRRADIUM_STUDY_MEMORY_LIMIT_MB", memory_mb)),
        progress_callback=report_material_progress,
    )
    _status(status_path, request, "processing-phits-dose-statistics", 92)
    summary = extreme_summary(values, relative_error, inside)
    material_statistics = []
    for label in model.material_labels:
        material_mask = inside & (material_ids == int(label["id"]))
        if not np.any(material_mask):
            continue
        material_statistics.append(
            {
                "id": int(label["id"]),
                "name": label["name"],
                **extreme_summary(values, relative_error, material_mask),
            }
        )
    relative_error_target = float(monte_carlo.get("relative_error_target", 0.03))
    all_cells_below_target = bool(
        summary.get("cells_with_error_estimate") == summary["active_cells"]
        and summary.get("relative_error_maximum") is not None
        and float(summary["relative_error_maximum"]) <= relative_error_target
    )
    from sim_gamma.simulators.openmc.structured_postprocess import (
        RegularMeshSpec,
        write_compressed_vti,
        write_isodose_cuts,
    )

    mesh = RegularMeshSpec(model.mesh.dimension, model.mesh.lower_left, model.mesh.upper_right)
    vtk_path = write_compressed_vti(
        values,
        mesh,
        output_root / "dose_distribution.vti",
        inside_mask=inside,
        material_ids=material_ids,
        relative_error=relative_error if np.any(np.isfinite(relative_error)) else None,
    )
    cuts = write_isodose_cuts(values, mesh, output_root, inside_mask=inside)
    visualization = {
        **summary,
        "vtk": vtk_path.name,
        "vtk_format": "vti",
        "mesh": {
            "dimension": list(model.mesh.dimension),
            "lower_left": list(model.mesh.lower_left),
            "upper_right": list(model.mesh.upper_right),
        },
        "materials": model.material_labels,
        "active_cells": int(np.count_nonzero(inside)),
        **cuts,
    }
    _write_json(output_root / "visualization.json", visualization)
    statistical_evaluation = {
        **summary,
        "mesh": visualization["mesh"],
        "cell_size_cm": list(model.mesh.spacing),
        "method": "phits-egs5-t-deposit-mesh",
        "materials": material_statistics,
        "expected_active_cells": int(np.count_nonzero(inside)),
        "all_cells_below_5_percent": bool(
            summary.get("cells_with_error_estimate") == summary["active_cells"]
            and summary.get("relative_error_maximum") is not None
            and float(summary["relative_error_maximum"]) <= 0.05
        ),
        "all_cells_below_target": all_cells_below_target,
    }
    fine_mesh_convergence = {
        key: summary.get(key)
        for key in (
            "relative_error_median",
            "relative_error_p90",
            "relative_error_maximum",
            "cells_relative_error_below_1_percent",
            "cells_relative_error_below_5_percent",
            "cells_relative_error_below_10_percent",
            "cells_with_error_estimate",
        )
    }
    fine_mesh_convergence.update(
        {
            "expected_active_cells": int(np.count_nonzero(inside)),
            "materials": material_statistics,
            "all_cells_below_target": all_cells_below_target,
        }
    )
    result = {
        **summary,
        "run_id": request["run_id"],
        "backend": "phits-3.341-egs5",
        "dose_tally": "phits",
        "calculation_mode": "phits-comparison",
        "dose_normalization": "t-deposit-unit-0-gy-per-source-times-3.6-to-kgy-per-hour",
        "reported_dose_medium": "load-materials",
        "independent_estimator": True,
        "uses_cross_mode_calibration": False,
        "egs5_enabled": True,
        "particles": actual_particles,
        "requested_max_particles": particles,
        "simulated_particles": actual_particles,
        "actual_batches": effective_batches,
        "total_batch_realizations": total_batch_realizations,
        "requested_batches": requested_batches,
        "chunk_batch_layouts": [
            {"histories_per_batch": histories, "batches": batches}
            for histories, batches in actual_chunk_batch_layouts
        ],
        "cell_size_cm": float(monte_carlo.get("cell_size_cm", 2.0)),
        "mesh_spacing_cm": list(model.mesh.spacing),
        "total_cells": model.mesh.cell_count,
        "active_cells": int(np.count_nonzero(inside)),
        "cpu_cores": cpu_cores,
        "cpu_threads_used": workers,
        "parallel_mode": runtime.parallel_mode,
        "parallel_worker_ranks": runtime.workers if runtime.parallel_mode == "mpi" else None,
        "parallel_total_ranks": runtime.workers + 1 if runtime.parallel_mode == "mpi" else None,
        "transport_wall_seconds": None if recovered_layouts else transport_seconds,
        "transport_reused": bool(recovered_layouts),
        "preparation_wall_seconds": preparation_seconds,
        "geometry_check_wall_seconds": geometry_check_seconds,
        "geometry_check_reused": geometry_check_reused,
        "postprocess_wall_seconds": time.perf_counter() - transport_started - transport_seconds,
        "total_wall_seconds": time.perf_counter() - started,
        "source_count": model.source_count,
        "photon_rate_per_second": model.photon_rate_per_second,
        "source_activity_reference_date": model.source_activity_reference_date,
        "input_parts": len(model.input_paths),
        "statistics_basis": summary["statistics_basis"],
        "variance_reduction": {
            **model.variance_reduction,
            "adaptive_statistics": False,
            "relative_error_target": relative_error_target,
            "requested_batches": requested_batches,
            "effective_batches": effective_batches,
            "effective_batches_per_chunk": effective_batches_per_chunk,
            "total_batch_realizations": total_batch_realizations,
        },
        "geometry_translation_cm": list(model.geometry_translation_cm),
        "geometry_step": "input/load.step",
        "cad_to_phits_manifest": str(cad_workflow.manifest_path.relative_to(case_root)),
        "phits_input_files": [str(path.relative_to(case_root)) for path in model.input_paths],
        "phits_tetrahedral_geometry": str(model.geometry_path.relative_to(case_root)),
        "materials": model.material_labels,
        "raw_fine_statistics": {
            key: summary.get(key)
            for key in (
                "minimum_dose",
                "maximum_dose",
                "average_dose",
                "dose_uniformity_ratio",
                "relative_error_median",
                "relative_error_p90",
                "relative_error_maximum",
            )
        },
        "fine_mesh_convergence": fine_mesh_convergence,
        "statistical_evaluation": statistical_evaluation,
        "adaptive_converged": all_cells_below_target,
        "visualization": visualization,
    }
    _write_json(output_root / "result.json", {**result, "irradiator_type": request.get("irradiator_type", "IV")})
    comparison_file = None
    comparison_references = [Path(path) for path in request.get("comparison_references", [])]
    if comparison_references:
        from .compare_reference import compare

        comparison_path = Path(
            request.get("comparison_output", case_root.parent / "comparison.json")
        )
        _write_json(comparison_path, compare(case_root, comparison_references))
        comparison_file = str(comparison_path)
    _write_json(
        status_path,
        {
            "case_id": request["case_id"],
            "run_id": request["run_id"],
            "state": "completed",
            "stage": "completed",
            "progress": 100,
            "execution_enabled": True,
            "dose_tally": "phits",
            "particles": particles,
            "simulated_particles": actual_particles,
            "cpu_threads_used": workers,
            "parallel_mode": runtime.parallel_mode,
            "parallel_worker_ranks": runtime.workers if runtime.parallel_mode == "mpi" else None,
            "parallel_total_ranks": runtime.workers + 1 if runtime.parallel_mode == "mpi" else None,
            "transport_wall_seconds": None if recovered_layouts else transport_seconds,
            "cell_size_cm": float(monte_carlo.get("cell_size_cm", 2.0)),
            "mesh_cells": model.mesh.cell_count,
            "active_load_cells": int(np.count_nonzero(inside)),
            "variance_reduction": model.variance_reduction,
            "result_file": "result.json",
            "comparison_file": comparison_file,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    worker_path.unlink(missing_ok=True)
