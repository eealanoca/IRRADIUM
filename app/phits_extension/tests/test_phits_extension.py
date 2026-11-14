import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from phits_extension.cad_workflow import prepare_phits_from_step
from phits_extension.availability import candidate_phits_roots, detect_phits_availability
from phits_extension.compare_reference import _read_vti
from phits_extension.model import (
    MeshDefinition,
    PhitsModel,
    _batch_layout,
    _focus_line_source_on_load,
    _mesh_for_bounds,
    _source_section,
    _source_records,
    _write_tetrahedral_bdf,
)
from phits_extension.postprocess import combine_phits_vtk, extreme_summary
from phits_extension.runner import resolve_runtime


class PhitsModelTests(unittest.TestCase):
    def test_geometry_cache_reuses_only_equivalent_validated_geometry(self):
        from phits_extension.worker_cli import _geometry_check
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inp = root / "production.inp"
            bdf = root / "load.bdf"
            bdf.write_text("validated tetrahedra")
            binary = root / "phits.exe"
            binary.touch()
            source = ("[ Material ]\nMAT[1] H 1\n[ Surface ]\n"
                      "  10 rpp -1 1 -1 1 -1 1\n  11 rpp -2 2 -2 2 -2 2\n"
                      "  20 rpp -10 10 -10 10 -10 10\n[ Cell ]\n1 1 -1 -10\n"
                      "[ T-Deposit ]\n  nx = 10\n[ End ]\n")
            inp.write_text(source)
            model = SimpleNamespace(input_path=inp, geometry_path=bdf, geometry_check_path=None,
                                    material_fraction_cache=root / "cache/fractions")
            runtime = SimpleNamespace(executable=binary, command_prefix=("bash", str(root / "phits.sh")))
            with patch("phits_extension.worker_cli._run_geometry_check") as run:
                self.assertFalse(_geometry_check(model, runtime))
                self.assertTrue(_geometry_check(model, runtime))
                # Moving a strictly enclosing air boundary is equivalent.
                inp.write_text(source.replace("-10 10 -10 10 -10 10", "-20 20 -20 20 -20 20"))
                self.assertTrue(_geometry_check(model, runtime))
                self.assertEqual(run.call_count, 1)
                # Clipping an inner box, changing a material or changing the
                # tetrahedra each requires a fresh check.
                inp.write_text(source.replace("-10 10 -10 10 -10 10", "-1 1 -1 1 -1 1"))
                self.assertFalse(_geometry_check(model, runtime))
                inp.write_text(source.replace("MAT[1] H 1", "MAT[1] O 1"))
                self.assertFalse(_geometry_check(model, runtime))
                bdf.write_text("different tetrahedra")
                self.assertFalse(_geometry_check(model, runtime))
                self.assertEqual(run.call_count, 4)

    def test_failed_geometry_check_is_never_cached(self):
        from phits_extension.worker_cli import _geometry_check
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model = SimpleNamespace(input_path=root / "input.inp", material_fraction_cache=root / "cache/fractions")
            model.input_path.write_text("source")
            with patch("phits_extension.worker_cli._geometry_check_signature", return_value="key"), \
                 patch("phits_extension.worker_cli._run_geometry_check", side_effect=ValueError("TETRA")):
                with self.assertRaisesRegex(ValueError, "TETRA"):
                    _geometry_check(model, SimpleNamespace())
            self.assertFalse((root / "cache/phits-geometry-checks/key.json").exists())

    def test_geometry_diagnostic_caps_resolution_without_changing_production(self):
        from phits_extension.worker_cli import _geometry_check
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            path = work / "production.inp"
            original = "  icntl = 0\n  itgchk = 0\n  vtkout = 0\n  epsout = 0\n  nx = 100\n  ny = 80\n  nz = 200\n  file = deposit-001.out\n"
            path.write_text(original)
            model = SimpleNamespace(input_path=path, geometry_check_path=None)
            runtime = SimpleNamespace(command_prefix=("bash", "/phits/bin/phits.sh"), workers=2)
            with patch("phits_extension.worker_cli.resolve_runtime", return_value=runtime), \
                 patch("phits_extension.worker_cli.run_input") as run:
                _geometry_check(model, runtime)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(path.read_text(), original)
            diagnostic = (work / "sim_gamma_geometry_check.inp").read_text()
            for axis in "xyz":
                self.assertIn(f"n{axis} = 24", diagnostic)
            self.assertIn("file = geometry-check.out", diagnostic)
            self.assertIn("itgchk = 1", (work / "sim_gamma_tetra_check.inp").read_text())

    def test_numeric_xy_tally_preserves_voxel_order_and_ignores_legend(self):
        from phits_extension.postprocess import _read_xy_tally

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "deposit-001.out"
            header = "# ( ( data(x,y), x = 1, nx ), y = ny, 1, -1 )\n\nhc: grid\n"
            path.write_text(header + "3 4\n1 2\nz: legend\nhc: legend\n0\n"
                            + header + "7 8\n5 6\nz: legend\n")
            mesh = MeshDefinition((2, 2, 2), (0, 0, 0), (2, 2, 2))
            self.assertEqual(_read_xy_tally(path, mesh).tolist(), list(range(1, 9)))
            path.write_text(header + "3 4\n1 2\n")
            with self.assertRaisesRegex(ValueError, "se esperaban 2"):
                _read_xy_tally(path, mesh)

    def test_resume_only_reuses_completed_transport_with_matching_input(self):
        from phits_extension.worker_cli import _recover_completed_transport

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / "001-c1/work/phits"
            new = root / "001-c2/work/phits"
            old.mkdir(parents=True)
            new.mkdir(parents=True)
            input_name = "sim_gamma_phits_001.inp"
            for work in (old, new):
                (work / "load.bdf.json").write_text('{"key":"mesh"}')
                (work / input_name).write_text("matching input")
            report = old / "phits-001.out"
            log = old / "sim_gamma_phits_001.log"
            report.write_text("Number of lost particles = 1\njob termination date\n"
                              "source: maxcas maxbch irskip\n--\n10 2 0\n")
            log.write_text("transport finished")
            for name in ("deposit-001.vtk", "deposit-001_err.vtk"):
                (old / name).write_text("vtk")
            model = SimpleNamespace(
                input_path=new / input_name, input_paths=(new / input_name,),
                mesh=SimpleNamespace(cell_count=2),
            )
            with patch("phits_extension.postprocess.read_deposit_pair"):
                self.assertEqual(_recover_completed_transport(model, root / "001-c2", 0), [])
                report.write_text(report.read_text().replace("particles = 1", "particles = 0"))
                log.touch()
                (new / input_name).write_text("different input")
                self.assertEqual(_recover_completed_transport(model, root / "001-c2", 0), [])
                (new / input_name).write_text("matching input")
                self.assertEqual(_recover_completed_transport(model, root / "001-c2", 0), [(10, 2)])
                self.assertTrue((new / "deposit-001_err.vtk").is_file())

    def test_phits_availability_reads_active_shell_profile_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            configured_root = home / "phits"
            (home / ".zshrc").write_text(
                '# export PHITSPATH="/ignored"\n'
                'export PHITSPATH="$HOME/phits"\n',
                encoding="utf-8",
            )

            roots = candidate_phits_roots(
                environment={"PATH": ""},
                home=home,
                default_root=None,
            )
            with patch(
                "phits_extension.availability.resolve_runtime",
                return_value=SimpleNamespace(parallel_mode="openmp"),
            ) as resolver:
                availability = detect_phits_availability(
                    environment={"PATH": ""},
                    home=home,
                    default_root=None,
                )

        self.assertEqual(roots, [configured_root.resolve()])
        self.assertTrue(availability.available)
        self.assertEqual(availability.parallel_mode, "openmp")
        resolver.assert_called_once_with(configured_root.resolve(), cpu_workers=1)

    def test_phits_availability_is_disabled_without_a_usable_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            availability = detect_phits_availability(
                environment={"PATH": ""},
                home=Path(temporary),
                default_root=None,
            )

        self.assertFalse(availability.available)
        self.assertIn("no está disponible", availability.message)

    def test_phits_batches_follow_requested_value_and_mpi_workers(self):
        histories, batches = _batch_layout(
            10_000_000,
            workers=11,
            preferred_batches=20,
        )

        self.assertEqual(batches, 22)
        self.assertEqual(histories, 454_546)
        self.assertEqual(histories * batches, 10_000_012)

    def test_reference_reader_honors_hidden_load_cells(self):
        from sim_gamma.simulators.openmc.structured_postprocess import (
            RegularMeshSpec,
            write_compressed_vti,
        )

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "dose.vti"
            mesh = RegularMeshSpec((2, 1, 1), (0, 0, 0), (2, 1, 1))
            write_compressed_vti(
                np.asarray([1.0, 2.0]),
                mesh,
                path,
                inside_mask=np.asarray([True, False]),
                material_ids=np.asarray([1, 1]),
            )

            _image, _dose, _error, inside, material_ids = _read_vti(path)

        self.assertEqual(inside.tolist(), [True, False])
        self.assertEqual(material_ids.tolist(), [1, 1])

    def test_phits_mesh_preserves_requested_spacing_like_openmc(self):
        mesh = _mesh_for_bounds(
            np.asarray([[-20.5, -186.99901123046877, -33.0], [20.5, -133.00098876953126, 33.0]]),
            3.0,
        )

        self.assertEqual(mesh.dimension, (14, 18, 22))
        np.testing.assert_allclose(mesh.lower_left, (-21.0, -187.0, -33.0))
        np.testing.assert_allclose(mesh.upper_right, (21.0, -133.0, 33.0))
        np.testing.assert_allclose(mesh.spacing, (3.0, 3.0, 3.0))

    def test_cad_workflow_records_the_automatic_step_to_phits_conversion(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            case = output / "runs" / "case"
            input_root = case / "input"
            deterministic_root = case / "deterministic"
            phits_root = case / "monte_carlo" / "work" / "phits"
            input_root.mkdir(parents=True)
            deterministic_root.mkdir()
            phits_root.mkdir(parents=True)
            (input_root / "load.step").write_text("STEP", encoding="utf-8")
            (input_root / "materials.json").write_text(
                json.dumps({"materials": [
                    {"name": "air", "elements": {"N": 1}, "density": 0.001},
                    {"name": "water", "elements": {"H": 2, "O": 1}, "density": 1.0},
                ]}),
                encoding="utf-8",
            )
            (input_root / "sources.json").write_text("{}", encoding="utf-8")
            (input_root / "simulation_parameters.json").write_text("{}", encoding="utf-8")
            (deterministic_root / "result.json").write_text("{}", encoding="utf-8")
            geometry = phits_root / "load.bdf"
            geometry.write_text("BDF", encoding="ascii")
            phits_input = phits_root / "sim_gamma_phits_001.inp"
            phits_input.write_text("[END]", encoding="utf-8")
            model = PhitsModel(
                input_paths=(phits_input,), geometry_path=geometry,
                mesh=MeshDefinition((2, 3, 4), (0, 0, 0), (2, 3, 4)),
                material_surfaces={"water": phits_root / "water.stl"},
                material_labels=[{"id": 1, "name": "water"}],
                phits_material_ids={"water": 2},
                geometry_translation_cm=(1, 2, 3), source_count=1,
                photon_rate_per_second=1, source_activity_reference_date="2026-09-19",
                variance_reduction={}, histories_per_batch=1, batches=1,
                chunk_batch_layouts=((1, 1),),
            )

            with patch(
                "sim_gamma.application.services.cad_preview.CadPreviewService.derive_component_meshes",
                return_value={"parts": [{"name": "water_001", "material": "water"}]},
            ), patch(
                "phits_extension.cad_workflow.prepare_phits_model", return_value=model
            ):
                workflow = prepare_phits_from_step(case, cpu_workers=4)

            manifest = json.loads(workflow.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["state"], "ready-for-phits-runtime")
            self.assertEqual(manifest["cad"]["components"][0]["material"], "water")
            self.assertEqual(manifest["geometry"]["material_mapping"][0]["phits_material_id"], 2)
            self.assertEqual(manifest["tally_mesh"]["cell_count"], 24)

    def test_phits_sources_focus_on_entire_load_and_preserve_physical_rate(self):
        from datetime import date

        document = {
            "radionuclides": {"Co-60": {
                "half_life": 1925.0,
                "emissions": [
                    {"type": "gamma", "value": 1.173, "probability": 1.0},
                    {"type": "gamma", "value": 1.332, "probability": 1.0},
                ],
            }},
            "sources": [{
                "lower_point": [-1000, 0, -100],
                "upper_point": [-1000, 0, 100],
                "initial_reading_date": date.today().isoformat(),
                "intensity": 100.0,
            }],
        }
        deterministic = {
            "geometric_center_cm": [0, 0, 0],
            "step_angle_degrees_best_real": [0],
            "best_length_points_mm": [0],
        }
        records, emissions, variance = _source_records(
            document, "Co-60", deterministic,
            np.asarray([[-10, -10, -10], [10, 10, 10]], dtype=float),
        )
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["directional"])
        self.assertEqual(records[0]["physical_strength"], 200.0)
        self.assertAlmostEqual(
            records[0]["strength"],
            200.0 * records[0]["solid_angle_fraction"],
        )
        self.assertEqual(sum(weight for _, weight in emissions), 1.0)
        self.assertEqual(
            variance["method"],
            "dynamic-load-cone-plus-photon-importance-splitting",
        )
        self.assertEqual(variance["focused_sources"], 1)
        self.assertAlmostEqual(variance["physical_photon_rate_per_second"], 200.0)
        self.assertGreater(variance["estimated_primary_sampling_gain"], 1.0)

    def test_dynamic_phits_cone_contains_all_load_corners_from_both_endpoints(self):
        lower = np.asarray([-100.0, 0.0, -10.0])
        upper = np.asarray([-100.0, 0.0, 10.0])
        bounds = np.asarray([[-10.0, -15.0, -20.0], [10.0, 15.0, 20.0]])

        focus = _focus_line_source_on_load(lower, upper, bounds)

        self.assertTrue(focus["directional"])
        axis = np.asarray(focus["axis"])
        half_angle = np.radians(focus["half_angle_degrees"])
        corners = np.asarray([
            [x, y, z]
            for x in bounds[:, 0]
            for y in bounds[:, 1]
            for z in bounds[:, 2]
        ])
        for point in (lower, upper):
            directions = corners - point
            directions /= np.linalg.norm(directions, axis=1)[:, None]
            angles = np.arccos(np.clip(directions @ axis, -1.0, 1.0))
            self.assertLessEqual(float(np.max(angles)), half_angle)
        self.assertAlmostEqual(
            focus["solid_angle_fraction"],
            0.5 * (1.0 - np.cos(half_angle)),
        )

    def test_phits_source_section_writes_dynamic_cone(self):
        lines = _source_section(
            [{
                "lower": np.asarray([-100.0, 0.0, -10.0]),
                "upper": np.asarray([-100.0, 0.0, 10.0]),
                "directional": True,
                "axis": np.asarray([1.0, 0.0, 0.0]),
                "half_angle_degrees": 20.0,
                "solid_angle_fraction": 0.0301536896,
                "strength": 3.01536896,
                "physical_strength": 100.0,
            }],
            [(1.173, 0.5), (1.332, 0.5)],
        )
        source = "\n".join(lines)
        self.assertIn("dir = 0", source)
        self.assertIn("phi = 0", source)
        self.assertIn("dom = 20", source)
        self.assertNotIn("dir = all", source)

    def test_main_worker_dispatches_phits_without_loading_openmc(self):
        from sim_gamma.workers.monte_carlo_cli import run

        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary) / "case"
            (case / "input").mkdir(parents=True)
            (case / "deterministic").mkdir()
            (case / "monte_carlo").mkdir()
            (case / "input" / "load.step").touch()
            (case / "input" / "simulation_parameters.json").write_text(
                json.dumps({"monte_carlo": {"dose_tally": "phits"}}), encoding="utf-8"
            )
            (case / "deterministic" / "result.json").write_text("{}", encoding="utf-8")
            request_path = case / "monte_carlo" / "request.json"
            request_path.write_text(json.dumps({
                "case_id": "case",
                "case_root": str(case),
                "run_id": "run",
            }), encoding="utf-8")
            with patch("phits_extension.worker_cli.run") as phits_run:
                run(request_path)
            phits_run.assert_called_once_with(request_path)

    def test_two_material_step_generates_distinct_tetrahedral_universes(self):
        import trimesh

        with tempfile.TemporaryDirectory() as temporary:
            water = trimesh.creation.box((4, 4, 4))
            water.apply_translation((-3, 0, 0))
            steel = trimesh.creation.box((4, 4, 4))
            steel.apply_translation((3, 0, 0))
            destination = Path(temporary) / "load.bdf"
            _write_tetrahedral_bdf(
                [
                    {"name": "water_001", "material": "water", "mesh": water},
                    {"name": "steel_002", "material": "steel", "mesh": steel},
                ],
                destination,
                {"water": 5001, "steel": 5002},
            )
            bdf = destination.read_text(encoding="ascii")
            self.assertIn("5001", bdf)
            self.assertIn("5002", bdf)

    def test_touching_native_solids_share_interface_nodes(self):
        import cadquery as cq
        import numpy as np
        from phits_extension.model import _nastran_number

        with tempfile.TemporaryDirectory() as temporary:
            first = cq.Workplane("XY").box(2, 2, 2).val()
            second = first.translate((2, 0, 0))
            target = Path(temporary) / "load.bdf"
            _write_tetrahedral_bdf([
                {"name": "water", "material": "water", "cad_shape": first},
                {"name": "plastic", "material": "plastic", "cad_shape": second},
            ], target, {"water": 5001, "plastic": 5002})
            nodes = {}
            cells = {5001: set(), 5002: set()}
            for line in target.read_text().splitlines():
                if line.startswith("GRID"):
                    nodes[int(line[8:16])] = np.array([_nastran_number(line[i:i + 8]) for i in (24, 32, 40)])
                if line.startswith("CTETRA"):
                    cells[int(line[16:24])].update(int(line[i:i + 8]) for i in (24, 32, 40, 48))
            shared = cells[5001] & cells[5002]
            self.assertGreaterEqual(len(shared), 4)
            self.assertTrue(all(abs(nodes[node][0] - 1) < 1e-7 for node in shared))

    def test_geometry_distance_errors_are_fatal_even_with_successful_exit(self):
        import subprocess
        from phits_extension.runner import PhitsRuntime, run_input

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "check.inp"
            source.write_text("[ Parameters ]\n  icntl = 8\n")
            runtime = PhitsRuntime(root / "phits", (str(root / "bin/phits.sh"),), "openmp", 1)
            output = "icntl = 8: Draw geometry by gshow option\n*** TETRA ERROR: fail to calculate distance."
            with patch("phits_extension.runner.subprocess.run",
                       return_value=subprocess.CompletedProcess([], 0, stdout=output)):
                with self.assertRaisesRegex(RuntimeError, "TETRA ERROR"):
                    run_input(runtime, source)

    def test_curved_shell_and_filling_share_a_conformal_interface(self):
        import cadquery as cq
        import numpy as np
        from phits_extension.model import _nastran_number

        with tempfile.TemporaryDirectory() as temporary:
            water = cq.Workplane("XY").circle(2).extrude(8).val()
            shell = cq.Workplane("XY").circle(2.1).circle(2).extrude(8).val()
            target = Path(temporary) / "cylinder.bdf"
            _write_tetrahedral_bdf([
                {"name": "filling", "material": "water", "cad_shape": water},
                {"name": "shell", "material": "plastic", "cad_shape": shell},
            ], target, {"water": 5001, "plastic": 5002})
            nodes, cells = {}, {5001: [], 5002: []}
            for line in target.read_text().splitlines():
                if line.startswith("GRID"):
                    nodes[int(line[8:16])] = np.array([_nastran_number(line[i:i + 8]) for i in (24, 32, 40)])
                elif line.startswith("CTETRA"):
                    cells[int(line[16:24])].append([int(line[i:i + 8]) for i in (24, 32, 40, 48)])
            water_nodes = {node for cell in cells[5001] for node in cell}
            shell_nodes = {node for cell in cells[5002] for node in cell}
            self.assertGreater(len(water_nodes & shell_nodes), 32)
            for material, shape in ((5001, water), (5002, shell)):
                volume = 0.0
                for cell in cells[material]:
                    points = np.array([nodes[node] for node in cell])
                    volume += abs(np.linalg.det(points[1:] - points[0])) / 6
                self.assertAlmostEqual(volume / shape.Volume(), 1.0, delta=.01)

    def test_streamed_instances_keep_unique_nodes_and_rigid_placement(self):
        import cadquery as cq
        import numpy as np
        from phits_extension.model import _nastran_number

        with tempfile.TemporaryDirectory() as temporary:
            box = cq.Workplane("XY").box(1, 2, 3).val().translate((1, 0, 0))
            target = Path(temporary) / "instances.bdf"
            _write_tetrahedral_bdf([
                {"name": "load", "material": "water", "cad_shape": box},
            ], target, {"water": 5001}, instances=[
                {"angle": 0, "translation": np.array([0, 0, 0])},
                {"angle": 180, "translation": np.array([4, 0, 0])},
            ])
            nodes, cells = {}, []
            for line in target.read_text().splitlines():
                if line.startswith("GRID"):
                    node_id = int(line[8:16])
                    self.assertNotIn(node_id, nodes)
                    nodes[node_id] = np.array([_nastran_number(line[i:i + 8]) for i in (24, 32, 40)])
                elif line.startswith("CTETRA"):
                    cells.append([int(line[i:i + 8]) for i in (24, 32, 40, 48)])
            first = [point for point in nodes.values() if point[0] < 2]
            second = [point for point in nodes.values() if point[0] > 2]
            self.assertEqual(len(first), len(second))
            self.assertTrue(np.allclose(np.min(first, axis=0), [.5, -1, -1.5]))
            self.assertTrue(np.allclose(np.max(second, axis=0), [3.5, 1, 1.5]))
            self.assertTrue(all(node in nodes for cell in cells for node in cell))
            volume = sum(abs(np.linalg.det(np.array([nodes[n] for n in cell])[1:] - nodes[cell[0]])) / 6
                         for cell in cells)
            self.assertAlmostEqual(volume, 2 * box.Volume(), places=3)

    def test_overlapping_cad_solids_are_rejected_before_transport(self):
        import cadquery as cq

        with tempfile.TemporaryDirectory() as temporary:
            box = cq.Workplane("XY").box(2, 2, 2).val()
            with self.assertRaisesRegex(ValueError, "se superponen"):
                _write_tetrahedral_bdf([
                    {"name": "water_1", "material": "water", "cad_shape": box},
                    {"name": "plastic_1", "material": "plastic", "cad_shape": box.translate((1, 0, 0))},
                ], Path(temporary) / "overlap.bdf", {"water": 5001, "plastic": 5002})

    def test_native_components_preserve_multisolid_material_assignment(self):
        import cadquery as cq
        from phits_extension.model import _translated_components

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cube = cq.Workplane("XY").box(10, 10, 10).val()
            assembly = cq.Assembly()
            assembly.add(cq.Compound.makeCompound([cube, cube.translate((20, 0, 0))]),
                         name="water_pair")
            assembly.export(str(root / "compound.step"))
            parts, *_ = _translated_components(root / "compound.step", root / "cache", root / "work", None)
            self.assertEqual(len(parts), 1)
            self.assertEqual(parts[0]["material"], "water")
            self.assertEqual(len(parts[0]["cad_shape"].Solids()), 2)
            self.assertAlmostEqual(parts[0]["cad_shape"].Volume(), 2)
            self.assertIn("cad_file", parts[0])

    def test_native_assembly_unit_conversion_preserves_curved_cad_volume(self):
        import json
        import math

        import cadquery as cq
        from phits_extension.model import _translated_components

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assembly = cq.Assembly()
            assembly.add(cq.Workplane("XY").circle(18).extrude(27), name="water_cylinder")
            source = root / "cylinder.step"
            assembly.export(str(source))
            parts, *_ = _translated_components(source, root / "cache", root / "work", (4, 5, 6))
            target = root / "load.bdf"
            _write_tetrahedral_bdf(parts, target, {"water": 5001})
            audit = json.loads(target.with_suffix(".audit.json").read_text())
            self.assertAlmostEqual(audit["regions"][0]["cad_volume_cm3"], math.pi * 1.8**2 * 2.7)
            self.assertLess(audit["regions"][0]["relative_volume_difference"], .01)

    def test_runtime_reports_incompatible_glibc(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bin").mkdir()
            (root / "bin" / "phits.sh").touch()
            executable = root / "bin" / "phits_omp_lin"
            executable.touch()
            with patch(
                "phits_extension.runner._glibc_incompatibility",
                return_value="GLIBC_2.39 not found",
            ):
                with self.assertRaisesRegex(RuntimeError, "GLIBC"):
                    resolve_runtime(root, 2)

    def test_runtime_selects_334_openmp_when_mpi_libraries_are_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bin").mkdir()
            (root / "bin" / "phits.sh").touch()
            (root / "bin" / "phits334_lin_mpi.exe").touch()
            (root / "bin" / "phits334_lin_openmp.exe").touch()
            with patch(
                "phits_extension.runner._glibc_incompatibility",
                side_effect=lambda path: "libmpi.so.12 => not found" if "mpi" in path.name else None,
            ):
                runtime = resolve_runtime(root, 12)
            self.assertEqual(runtime.parallel_mode, "openmp")
            self.assertEqual(runtime.workers, 12)
            self.assertEqual(runtime.executable.name, "phits334_lin_openmp.exe")

    def test_runtime_reserves_one_mpi_slot_for_phits_master(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bin").mkdir()
            (root / "bin" / "phits.sh").touch()
            (root / "bin" / "phits334_lin_mpi.exe").touch()
            with patch("phits_extension.runner.shutil.which", return_value="mpiexec"), patch(
                "phits_extension.runner._local_mpi_socket_available", return_value=True
            ), patch(
                "phits_extension.runner._glibc_incompatibility", return_value=None
            ), patch(
                "phits_extension.runner._available_cpu_slots", return_value=12
            ):
                runtime = resolve_runtime(root, 12)
            self.assertEqual(runtime.parallel_mode, "mpi")
            self.assertEqual(runtime.workers, 11)


class PhitsPostprocessTests(unittest.TestCase):
    def test_step_mask_retains_edge_cells_mislabeled_air_by_phits_334(self):
        import trimesh
        import vtk
        from vtk.util.numpy_support import numpy_to_vtk

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "case" / "monte_carlo" / "work" / "phits"
            work.mkdir(parents=True)
            surface = root / "water.stl"
            solid = trimesh.creation.box((2, 1, 1))
            solid.apply_translation((1, 0.5, 0.5))
            solid.export(surface)
            input_path = work / "sim_gamma_phits_001.inp"
            input_path.touch()
            grid = vtk.vtkImageData()
            grid.SetDimensions(3, 2, 2)
            for name, data in (("all", [1.0, 2.0]), ("material", [1, 2])):
                array = numpy_to_vtk(np.asarray(data, dtype=np.float64), deep=True)
                array.SetName(name)
                grid.GetCellData().AddArray(array)
            writer = vtk.vtkDataSetWriter()
            writer.SetFileName(str(work / "deposit-001.vtk"))
            writer.SetInputData(grid)
            self.assertEqual(writer.Write(), 1)
            model = PhitsModel(
                input_paths=(input_path,), geometry_path=work / "load.bdf",
                mesh=MeshDefinition((2, 1, 1), (0, 0, 0), (2, 1, 1)),
                material_surfaces={"water": surface},
                material_labels=[{"id": 1, "name": "water"}],
                phits_material_ids={"water": 2},
                geometry_translation_cm=(0, 0, 0), source_count=1,
                photon_rate_per_second=1, source_activity_reference_date="2026-09-02", variance_reduction={},
                histories_per_batch=1, batches=1, chunk_batch_layouts=((1, 1),),
            )
            values, _, material_ids, inside = combine_phits_vtk(model)
            self.assertEqual(values.tolist(), [1.0, 2.0])
            self.assertEqual(material_ids.tolist(), [1, 1])
            self.assertEqual(inside.tolist(), [True, True])

    def test_combines_chunks_and_uses_raw_load_extremes(self):
        import vtk
        from vtk.util.numpy_support import numpy_to_vtk

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = []
            for index, values in enumerate(([1.0, 2.0], [0.5, 1.0]), start=1):
                input_path = root / f"sim_gamma_phits_{index:03d}.inp"
                input_path.touch()
                inputs.append(input_path)
                grid = vtk.vtkImageData()
                grid.SetDimensions(3, 2, 2)
                dose = numpy_to_vtk(np.asarray(values, dtype=np.float64), deep=True)
                dose.SetName("dose")
                grid.GetCellData().AddArray(dose)
                material = numpy_to_vtk(np.asarray([2, 1], dtype=np.int32), deep=True)
                material.SetName("material")
                grid.GetCellData().AddArray(material)
                writer = vtk.vtkDataSetWriter()
                writer.SetFileName(str(root / f"deposit-{index:03d}.vtk"))
                writer.SetInputData(grid)
                writer.SetFileTypeToBinary()
                self.assertEqual(writer.Write(), 1)
                error_grid = vtk.vtkImageData()
                error_grid.SetDimensions(3, 2, 2)
                error_values = numpy_to_vtk(np.asarray([0.2, 0.4], dtype=np.float64), deep=True)
                error_values.SetName("all")
                error_grid.GetCellData().AddArray(error_values)
                error_writer = vtk.vtkDataSetWriter()
                error_writer.SetFileName(str(root / f"deposit-{index:03d}_err.vtk"))
                error_writer.SetInputData(error_grid)
                error_writer.SetFileTypeToBinary()
                self.assertEqual(error_writer.Write(), 1)
            model = PhitsModel(
                input_paths=tuple(inputs),
                geometry_path=root / "load.bdf",
                mesh=MeshDefinition((2, 1, 1), (0, 0, 0), (2, 1, 1)),
                material_surfaces={},
                material_labels=[{"id": 1, "name": "water"}],
                phits_material_ids={"water": 2},
                geometry_translation_cm=(0, 0, 0),
                source_count=1,
                photon_rate_per_second=1,
                source_activity_reference_date="2026-09-02",
                variance_reduction={},
                histories_per_batch=1,
                batches=1,
                chunk_batch_layouts=((1, 1), (1, 1)),
            )
            values, errors, material_ids, inside = combine_phits_vtk(model)
            self.assertEqual(values.tolist(), [1.5, 0.0])
            summary = extreme_summary(values, errors, inside)
            self.assertEqual(summary["minimum_dose"], 1.5)
            self.assertEqual(summary["maximum_dose"], 1.5)
            self.assertAlmostEqual(summary["relative_error_maximum"], np.sqrt(0.05) / 1.5)
            self.assertEqual(summary["dose_p05"], 1.5)
            self.assertEqual(summary["dose_p95"], 1.5)
            self.assertEqual(summary["operational_dose_uniformity_ratio"], 1.0)
            self.assertEqual(summary["robust_dose_uniformity_ratio"], 1.0)
            self.assertEqual(summary["relative_error_median"], summary["relative_error_maximum"])


if __name__ == "__main__":
    unittest.main()
