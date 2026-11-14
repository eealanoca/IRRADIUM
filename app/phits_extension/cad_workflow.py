"""Flujo reproducible para convertir la carga STEP de un caso en PHITS.

El conversor mantiene en un solo contrato las operaciones que PHITS necesita:
componentes CAD, materiales, cambio de unidades, posicionamiento, tetraedros BDF,
malla de tally y archivos de entrada.  La física de transporte sigue aislada en
``model.py`` y ``runner.py``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .model import PhitsModel, prepare_phits_model


@dataclass(frozen=True)
class CadToPhitsWorkflow:
    model: PhitsModel
    manifest_path: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, document: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, ensure_ascii=False, indent=2, allow_nan=False)
    temporary.replace(path)


def prepare_phits_from_step(
    case_root: Path,
    phits_root: Path | None = None,
    cpu_workers: int = 1,
    route_context=None,
    output_root: Path | None = None,
) -> CadToPhitsWorkflow:
    """Convierte automáticamente el STEP guardado en un modelo PHITS listo.

    El usuario no necesita exportar BDF, STL intermedios ni editar tarjetas
    PHITS. Los nombres/materiales del STEP se validan contra ``materials.json``
    y el resultado queda auditado en ``cad_to_phits_manifest.json``.
    """

    from sim_gamma.application.services.cad_preview import CadPreviewService

    case_root = Path(case_root).resolve()
    input_root = case_root / "input"
    required_inputs = {
        "step": input_root / "load.step",
        "materials": input_root / "materials.json",
        "sources": input_root / "sources.json",
        "simulation": input_root / "simulation_parameters.json",
        **({"deterministic_result": case_root / "deterministic" / "result.json"}
           if route_context is None else {"route": input_root / "route.json"}),
    }
    missing_files = [name for name, path in required_inputs.items() if not path.is_file()]
    if missing_files:
        raise FileNotFoundError(
            "Entradas faltantes para STEP → PHITS: " + ", ".join(missing_files)
        )

    preview = CadPreviewService(
        case_root.parent.parent / "cache" / "phits-step-components"
    ).derive_component_meshes(required_inputs["step"])
    components = [
        {
            "name": str(part["name"]),
            "material": str(part["material"]).strip().lower(),
        }
        for part in preview["parts"]
    ]
    required_materials = {"air"} | {
        part["material"] for part in components if part["material"] != "air"
    }
    materials_document = json.loads(
        required_inputs["materials"].read_text(encoding="utf-8")
    )
    configured_materials = {
        str(item["name"]).strip().lower()
        for item in materials_document.get("materials", [])
    }
    missing_materials = sorted(required_materials - configured_materials)
    if missing_materials:
        raise ValueError(
            "Faltan composiciones para los materiales STEP: "
            + ", ".join(missing_materials)
        )

    geometry_path = (output_root or case_root / "monte_carlo") / "work" / "phits" / "load.bdf"
    previous_geometry_mtime = (
        geometry_path.stat().st_mtime_ns if geometry_path.is_file() else None
    )
    model = prepare_phits_model(
        case_root,
        phits_root=phits_root,
        cpu_workers=cpu_workers,
        route_context=route_context,
        output_root=output_root,
    )
    geometry_reused = bool(
        previous_geometry_mtime is not None
        and model.geometry_path.stat().st_mtime_ns == previous_geometry_mtime
    )

    manifest_path = model.geometry_path.parent / "cad_to_phits_manifest.json"
    material_mapping = []
    for index, label in enumerate(model.material_labels, start=1):
        material_mapping.append(
            {
                "name": label["name"],
                "result_material_id": int(label["id"]),
                "phits_material_id": int(model.phits_material_ids[label["name"]]),
                "phits_tetrahedral_universe": 5000 + index,
            }
        )
    document = {
        "schema_version": 1,
        "state": "ready-for-phits-runtime",
        "workflow": "cad-step-to-phits-tetrahedral-egs5",
        "workflow_steps": [
            "validate-case-inputs",
            "extract-step-components",
            "map-step-materials",
            "convert-millimetres-to-centimetres",
            "translate-load-to-work-section",
            "tetrahedralize-components-with-gmsh",
            "write-nastran-bdf-universes",
            "build-phits-world-sources-and-t-deposit",
        ],
        "inputs": {
            name: {
                "file": str(path.relative_to(case_root)),
                "sha256": _sha256(path),
            }
            for name, path in required_inputs.items()
        },
        "cad": {
            "format": "STEP",
            "source_units": "mm",
            "phits_units": "cm",
            "length_scale": 0.1,
            "component_count": len(components),
            "components": components,
            "translation_cm": list(model.geometry_translation_cm),
        },
        "geometry": {
            "format": "NASTRAN-BDF-tetrahedral",
            "file": str(model.geometry_path.relative_to(case_root)),
            "cache_reused": geometry_reused,
            "material_mapping": material_mapping,
            "volume_audit": (
                json.loads(model.geometry_path.with_suffix(".audit.json").read_text())
                if model.geometry_path.with_suffix(".audit.json").is_file() else None
            ),
        },
        "tally_mesh": {
            "dimension": list(model.mesh.dimension),
            "lower_left_cm": list(model.mesh.lower_left),
            "upper_right_cm": list(model.mesh.upper_right),
            "spacing_cm": list(model.mesh.spacing),
            "cell_count": model.mesh.cell_count,
        },
        "phits_inputs": [str(path.relative_to(case_root)) for path in model.input_paths],
    }
    _write_json(manifest_path, document)
    return CadToPhitsWorkflow(model=model, manifest_path=manifest_path)

