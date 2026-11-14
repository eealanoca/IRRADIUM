"""Prueba aislada y pequeña del proceso determinista; no usa OpenMC."""

import json
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from sim_gamma.application.services.case_store import CaseStore
from sim_gamma.workers.deterministic_cli import run


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="sim-gamma-deterministic-") as temporary:
        root = Path(temporary)
        config_root = root / "config"
        config_root.mkdir()
        for filename in ("sources.json", "materials.json", "work_sections.json"):
            shutil.copy2(PROJECT_ROOT / "data" / "config" / filename, config_root / filename)
        step_path = root / "load.step"
        shutil.copy2(PROJECT_ROOT / "data" / "examples" / "load.step", step_path)

        store = CaseStore(root / "output", config_root, step_path)
        case = store.create("smoke-deterministic", "Prueba aislada sin Monte Carlo.")
        case_root = store.root(case["case_id"])
        simulation_path = case_root / "input" / "simulation_parameters.json"
        simulation = json.loads(simulation_path.read_text())
        simulation["deterministic"]["num_points"] = 100
        simulation["compute"]["cpu_cores"] = 1
        simulation["movement"].update({
            "machine_angle_step_degrees": 90,
            "angular_positions_range": [1, 3],
            "length_complete_lower_range_mm": [-200, 0],
            "length_complete_upper_range_mm": [0, 200],
            "resolution_length_mm": 200,
        })
        simulation_path.write_text(json.dumps(simulation))
        request_path = case_root / "deterministic" / "request.json"
        request_path.write_text(
            json.dumps(
                {
                    "case_id": case["case_id"],
                    "run_id": uuid4().hex,
                    "irradiator_type": "IV",
                    "case_root": str(case_root),
                    "project_root": str(PROJECT_ROOT),
                    "parameters": {
                        "num_points": 100,
                        "cores": 1,
                        "work_section": "A",
                        "angle_degrees": 0,
                    },
                }
            ),
            encoding="utf-8",
        )

        run(request_path)

        status = json.loads(
            (case_root / "deterministic" / "status.json").read_text(encoding="utf-8")
        )
        result = json.loads(
            (case_root / "deterministic" / "result.json").read_text(encoding="utf-8")
        )
        assert result["irradiator_type"] == "IV"
        assert status["state"] == "completed"
        assert status["monte_carlo_started"] is False
        assert result["step_angle_degrees_best_real"]
        assert result["best_length_points_mm"]
        assert "average_dose" in result
        assert (case_root / "deterministic" / "dose_distribution.vtk").is_file()
        assert (case_root / "deterministic" / "isodose_xy.png").is_file()
        assert (case_root / "deterministic" / "isodose_yz.png").is_file()
        assert result["visualization"]["cuts"] == {
            "xy": "isodose_xy.png",
            "yz": "isodose_yz.png",
        }
        print("Deterministic smoke: OK")


if __name__ == "__main__":
    main()
