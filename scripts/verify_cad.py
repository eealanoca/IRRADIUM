from pathlib import Path
import subprocess
import tempfile
import h5py
from control import runtime_environment
from sim_gamma.simulators.openmc.engine import _run_isolated_cad_converter, CAD_PYTHON

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(dir=root / ".local/tmp", prefix="cad-check-") as temporary:
    directory = Path(temporary)
    subprocess.run([str(CAD_PYTHON), "-c", "import cadquery as cq,sys; cq.exporters.export(cq.Workplane('XY').box(10,10,10),sys.argv[1])", str(directory / "cube.step")], env=runtime_environment("cad"), check=True)
    _run_isolated_cad_converter(directory, "cube", ["water"])
    with h5py.File(directory / "cube.h5m", "r") as model:
        assert "tstt" in model
    print("Conversión real STEP → DAGMC con runtimes incluidos: OK")
