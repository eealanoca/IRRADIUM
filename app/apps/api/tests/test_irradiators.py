import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from apps.api.sim_gamma_api.main import app
from apps.api.sim_gamma_api.services import _instances
from apps.api.sim_gamma_api.settings import settings
from sim_gamma.application.services.case_store import CaseStore
from sim_gamma.domain.irradiators import current_irradiator


class IrradiatorApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {"IRRADIUM_WORKSPACE_ROOT": str(self.workspace)})
        self.environment.start()
        _instances.clear()
        self.config = self.workspace / "irradiators/IV/Configuracion"
        self.config.mkdir(parents=True)
        for path in (settings.project_root / "data/config").glob("*.json"):
            shutil.copy2(path, self.config / path.name)
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        _instances.clear()
        self.environment.stop()
        self.temporary.cleanup()

    def test_catalog_exposes_four_categories_and_only_iv_is_available(self):
        response = self.client.get("/api/irradiators")
        self.assertEqual(response.status_code, 200)
        items = response.json()["irradiators"]
        self.assertEqual([item["id"] for item in items], ["I", "II", "III", "IV"])
        self.assertEqual([item["id"] for item in items if item["enabled"]], ["IV"])

    def test_disabled_categories_cannot_list_create_or_read_results(self):
        for category in ("I", "II", "III"):
            for path in ("/api/cases", "/api/results/latest/movements"):
                self.assertEqual(self.client.get(f"/irradiators/{category}{path}").status_code, 409)
            response = self.client.post(f"/irradiators/{category}/api/cases", json={"name": "x", "description": "x"})
            self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.get("/irradiators/unknown/api/cases").status_code, 404)
        self.assertEqual(self.client.get("/api/health").json()["irradiator_type"], "IV")

    def test_case_and_result_catalog_keep_the_category_and_own_directory(self):
        response = self.client.post("/irradiators/IV/api/cases", json={"name": "Caso IV", "description": "Prueba", "irradiator_type": "IV"})
        self.assertEqual(response.status_code, 201, response.text)
        case = response.json()
        self.assertEqual(case["irradiator_type"], "IV")
        root = self.workspace / "irradiators/IV/Casos" / case["case_id"]
        self.assertTrue((root / "manifest.json").is_file())
        catalog = self.client.get(f"/irradiators/IV/api/results/cases/{case['case_id']}")
        self.assertEqual(catalog.status_code, 200, catalog.text)
        self.assertEqual(catalog.json()["irradiator_type"], "IV")
        listing = self.client.get("/irradiators/IV/api/cases").json()
        self.assertEqual(listing["irradiator_type"], "IV")
        self.assertEqual(len(listing["cases"]), 1)
        mismatch = self.client.post("/irradiators/IV/api/cases", json={"name": "x", "description": "x", "irradiator_type": "II"})
        self.assertEqual(mismatch.status_code, 422)
        mismatch_update = self.client.put(f"/irradiators/IV/api/cases/{case['case_id']}", json={"name": "x", "description": "x", "irradiator_type": "II", "deterministic_parameters": {}})
        self.assertEqual(mismatch_update.status_code, 422)

    def test_suspended_case_snapshots_the_route_and_calculates_dose_or_time(self):
        base = "/irradiators/IV/api/route"
        positions = [
            {"name": "Entrada", "center_mm": [100, 0, 10], "yaw_degrees": 0},
            {"name": "Salida", "center_mm": [0, 100, 10], "yaw_degrees": 90},
        ]
        self.assertEqual(self.client.get(base + "/configuration").json()["positions"], [])
        response = self.client.put(base + "/configuration", json={"positions": positions})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue((self.config / "route_parameters.json").is_file())
        self.assertEqual(self.client.get("/irradiators/III/api/route/configuration").status_code, 409)
        with patch(
            "sim_gamma.application.services.cad_preview.CadPreviewService.derive_component_meshes",
            return_value={"parts": [{"material": "water"}]},
        ):
            response = self.client.post(base + "/cases", data={
                "name": "Prueba de recorrido",
                "variance_reduction": "source_mixture",
                "materials_json": json.dumps([{"name": "water", "elements": {"H": 2, "O": 1}, "density": 1}]),
            }, files={"step": ("producto.step", b"STEP test fixture")})
        self.assertEqual(response.status_code, 201, response.text)
        case = response.json()
        case_id = case["case_id"]
        root = self.workspace / "irradiators/IV/Casos" / case_id
        self.assertEqual(json.loads((root / "input/route.json").read_text())["positions"], positions)
        self.assertEqual(json.loads((root / "input/route.json").read_text())["variance_reduction"], "source_mixture")
        self.assertTrue((root / "input/sources.json").is_file())
        self.assertEqual((root / "input/load.step").read_bytes(), b"STEP test fixture")
        self.assertFalse((root / "input/world_load.stl").exists())
        self.assertEqual(self.client.post(f"/api/cases/{case_id}/results/save").status_code, 409)

        (root / "monte_carlo/status.json").write_text(json.dumps({"state": "completed", "run_id": "test"}))
        (root / "results/route_rates.json").write_text(json.dumps({
            "run_id": "test",
            "positions": [{**position, "mean_kgy_h": rate} for position, rate in zip(positions, [2, 4])],
        }))
        path = f"{base}/cases/{case_id}/dose"
        inputs = {"start": 0, "end": 1, "run_id": "test"}
        for settings, dose, minutes in (
            ({"mode": "total", "value": 60}, 3, 60),
            ({"mode": "uniform", "value": 30}, 3, 60),
            ({"mode": "by_position", "times_minutes": [60, 30]}, 4, 90),
            ({"mode": "target_dose", "value": 6, "save": True}, 6, 120),
        ):
            response = self.client.post(path, json={**inputs, **settings})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertAlmostEqual(response.json()["dose_kgy"], dose)
            self.assertAlmostEqual(response.json()["total_minutes"], minutes)
        scenario_id = response.json()["scenario_id"]
        self.assertTrue((root / f"results/dose_scenarios/{scenario_id}.json").is_file())
        self.assertEqual(self.client.post(path, json={**inputs, "end": 2, "mode": "total", "value": 60}).status_code, 422)
        self.assertEqual(self.client.post(path, json={**inputs, "run_id": "other", "mode": "total", "value": 60}).status_code, 409)
        self.assertFalse((root / "results/movements_parameters_optimized.json").exists())

    def test_target_dose_uses_extremes_of_the_aligned_map_and_preserves_mean(self):
        from sim_gamma.domain.route_study import precision_status
        from sim_gamma.simulators.openmc.structured_postprocess import RegularMeshSpec, write_compressed_vti

        base = "/irradiators/IV/api"
        positions = [
            {"name": "I7", "center_mm": [0, 0, 0], "yaw_degrees": 0},
            {"name": "I8", "center_mm": [500, 0, 0], "yaw_degrees": 180},
        ]
        self.client.put(base + "/route/configuration", json={"positions": positions}).raise_for_status()
        with patch("sim_gamma.application.services.cad_preview.CadPreviewService.derive_component_meshes",
                   return_value={"parts": [{"material": "water"}]}):
            response = self.client.post(base + "/route/cases", data={
                "name": "Objetivo espacial", "materials_json": json.dumps([
                    {"name": "water", "elements": {"H": 2, "O": 1}, "density": 1},
                ]),
            }, files={"step": ("water.step", b"STEP fixture")})
        self.assertEqual(response.status_code, 201, response.text)
        case_id = response.json()["case_id"]
        root = self.workspace / "irradiators/IV/Casos" / case_id
        mesh = RegularMeshSpec((2, 1, 1), (0, 0, 0), (4, 2, 2))
        precision = precision_status({"active_cells": 2, "cells_with_error_estimate": 2, "relative_error_maximum": .01})
        for index, values in enumerate(([1, 5], [3, 7])):
            directory = root / "results" / f"position-{index}"
            directory.mkdir()
            write_compressed_vti(values, mesh, directory / "dose_distribution.vti", relative_error=[.01, .01])
            positions[index].update(mean_kgy_h=sum(values) / 2, precision=precision,
                                    summary=f"results/position-{index}/result.json")
        (root / "results/route_rates.json").write_text(json.dumps({
            "run_id": "target-test", "complete": True, "positions": positions,
        }))
        endpoint = base + f"/route/cases/{case_id}/dose"
        for metric, minutes, mean, minimum, maximum in (
            ("minimum", 60, 4, 2, 6),
            ("mean", 30, 2, 1, 3),
            ("maximum", 20, 4 / 3, 2 / 3, 2),
        ):
            response = self.client.post(endpoint, json={
                "run_id": "target-test", "start": 0, "end": 1, "mode": "target_dose",
                "value": 2, "target_metric": metric, "dose_map": True,
            })
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertAlmostEqual(result["total_minutes"], minutes)
            self.assertAlmostEqual(result["times_minutes"][0], minutes / 2)
            self.assertAlmostEqual(result["dose_kgy"], mean)
            self.assertAlmostEqual(result["dose_map"]["minimum_kgy"], minimum)
            self.assertAlmostEqual(result["dose_map"]["maximum_kgy"], maximum)
            self.assertEqual(result["target_metric"], metric)
            saved = json.loads((root / f"results/dose_scenarios/{result['scenario_id']}.json").read_text())
            self.assertEqual(saved["request"]["target_metric"], metric)
            self.assertEqual(len(result["dose_map"]["alignment"]["turns"]), 1)
            analysis = self.client.get(base + f"/route/cases/{case_id}/scenarios/{result['scenario_id']}/analysis")
            self.assertEqual(analysis.status_code, 200, analysis.text)
            self.assertAlmostEqual(analysis.json()["summary"]["average_dose"], result["dose_kgy"], places=6)
        for index in range(2):
            write_compressed_vti([0, 5], mesh, root / f"results/position-{index}/dose_distribution.vti")
        response = self.client.post(endpoint, json={
            "run_id": "target-test", "start": 0, "end": 1, "mode": "target_dose",
            "value": 2, "target_metric": "minimum",
        })
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("nula", response.json()["detail"])

    def test_suspended_source_uses_two_vertical_endpoints_and_fixed_diameter(self):
        source = {
            "identifier": "Prueba",
            "lower_point": [1, 2, 3],
            "upper_point": [1, 2, 83],
            "initial_activity_kci": 1,
            "initial_reading_date": "2025-01-01",
        }
        path = "/irradiators/IV/api/config/sources"
        response = self.client.post(path, json=source)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["sources"][-1]["diameter_mm"], 11)
        self.assertEqual(self.client.post(path, json={**source, "upper_point": [0, 0, 83]}).status_code, 422)

    def test_latest_trajectory_paths_are_distinct_for_all_categories(self):
        paths = []
        for category in ("I", "II", "III", "IV"):
            token = current_irradiator.set(category)
            try:
                paths.append(settings.movement_output)
            finally:
                current_irradiator.reset(token)
        self.assertEqual(len(set(paths)), 4)
        self.assertTrue(all(path.is_relative_to(self.workspace / "irradiators") for path in paths))

    def test_preferences_are_saved_in_a_file_instead_of_browser_storage(self):
        path = self.workspace / "preferences.json"
        with patch("apps.api.sim_gamma_api.main._preferences_path", return_value=path):
            self.assertEqual(self.client.get("/irradiators/IV/api/preferences").json(), {"theme": None})
            response = self.client.put("/irradiators/IV/api/preferences", json={"theme": "dark"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(json.loads(path.read_text()), {"theme": "dark"})
            self.assertEqual(self.client.get("/irradiators/IV/api/preferences").json(), {"theme": "dark"})
            self.assertEqual(self.client.put("/irradiators/IV/api/preferences", json={"theme": "invalid"}).status_code, 422)

    def test_preferences_use_the_preservable_user_data_folder(self):
        path = self.workspace / "Configuracion/preferencias.json"
        response = self.client.put('/api/preferences', json={'theme': 'dark'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(path.read_text()), {'theme': 'dark'})
        self.assertEqual(self.client.get('/api/preferences').json(), {'theme': 'dark'})

    def test_store_rejects_a_manifest_from_another_category(self):
        store = CaseStore(self.workspace / "irradiators/IV", self.config, Path("missing.step"), irradiator_type="IV")
        case = store.create("IV")
        other = CaseStore(self.workspace / "irradiators/II", self.config, Path("missing.step"), irradiator_type="II")
        destination = other.runs_root / case["case_id"]
        shutil.copytree(store.root(case["case_id"]), destination)
        with self.assertRaises(FileNotFoundError):
            other.root(case["case_id"])
        self.assertEqual(other.list(), [])


if __name__ == "__main__":
    unittest.main()
