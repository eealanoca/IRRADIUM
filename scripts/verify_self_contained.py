"""Run through ./irradium exec python ../scripts/verify_self_contained.py."""
from pathlib import Path
import errno
import json
import os
import shutil
import subprocess
import sys

import sim_gamma
import openmc
import vtk
from apps.api.sim_gamma_api.settings import settings
from sim_gamma.domain.irradiators import catalog, current_irradiator
from sim_gamma.simulators.openmc.engine import CAD_PYTHON

root = Path(__file__).resolve().parents[1]
assert Path(sys.prefix) == root / "runtime/main"
assert Path(sim_gamma.__file__).is_relative_to(root / "app/src")
assert Path(openmc.__file__).is_relative_to(root / "runtime/main")
assert Path(vtk.__file__).is_relative_to(root / "runtime/main")
assert CAD_PYTHON == root / "runtime/cad/bin/python"
assert shutil.which("conda") is None
assert not Path("/home/efr_al/anaconda3/bin/conda").exists()
assert not os.access("/home/efr_al/anaconda3", os.W_OK)
assert [item["id"] for item in catalog() if item["enabled"]] == ["IV"]
assert settings.output_root == root / "workspace/irradiators/IV"
assert settings.movement_output.is_relative_to(root / "workspace/irradiators/IV")
assert (root / "workspace/Configuracion/instalacion.json").is_file()
assert (root / "Iniciar_IRRADIUM.sh").is_file()
assert (root / "Desinstalar_IRRADIUM.sh").is_file()
assert (root / "Desinstalar_IRRADIUM.desktop").is_file()
from apps.api.sim_gamma_api.main import _preferences_path
assert _preferences_path() == root / "workspace/Configuracion/preferencias.json"
assert "/home/efr_al/anaconda3/" not in Path("/proc/self/maps").read_text()
try:
    descriptor = os.open("/usr/bin/env", os.O_WRONLY)
except OSError as error:
    assert error.errno in {errno.EROFS, errno.EACCES, errno.EPERM}
else:
    os.close(descriptor)
    raise AssertionError("Se permitió abrir para escritura un archivo externo")
manager = root / "runtime/bin/micromamba"
manager_version = subprocess.check_output([str(manager), "--version"], text=True).strip()
report = {"application": "IRRADIUM", "runtime": sys.prefix, "cad_python": str(CAD_PYTHON),
          "package_manager": str(manager), "package_manager_version": manager_version,
          "external_conda_visible": False, "external_file_writes_blocked": True,
          "enabled_categories": ["IV"], "category_root": str(settings.output_root)}
(root / ".local/state/self-contained.json").write_text(json.dumps(report, indent=2) + "\n")
print("IRRADIUM autocontenido y protegido; Anaconda oculto; categoría IV independiente: OK")
