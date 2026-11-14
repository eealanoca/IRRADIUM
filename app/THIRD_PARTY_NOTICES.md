# Avisos de componentes de terceros

El código propio de SIM Gamma se publica bajo la licencia MIT incluida en `LICENSE`.

SIM Gamma depende de proyectos con licencias propias, entre ellos Python, NumPy, SciPy, pandas, Matplotlib, VTK, FastAPI, Uvicorn, OpenMC, DAGMC, MOAB, CAD-to-OpenMC, Node.js, React, Next.js, vinext y VTK.js.

El instalador obtiene paquetes desde Conda y npm. La lista exacta y sus versiones se encuentra en `environment.yml`, `environment-cad-converter.yml`, `requirements.txt` y `apps/web/package-lock.json`. Cada publicación debe generar un inventario de dependencias y conservar sus textos de licencia cuando corresponda.

PHITS y EGS5 son componentes externos. No se incluyen, descargan ni redistribuyen. Los datos nucleares no se incluyen en el archivo tar.gz; el instalador los descarga de OpenMC y conserva sus atribuciones. Véase DATA_NOTICES.md en la raíz.

## Herramientas incluidas por IRRADIUM

Micromamba: gestor del proyecto Mamba, incluido como ejecutable local.
Origen: https://micro.mamba.pm/api/micromamba/linux-64/latest

Bubblewrap: protección de archivos, incluida en runtime/guard junto a sus
bibliotecas auxiliares. El aviso de copyright está en
`runtime/licenses/bubblewrap-copyright`. Las bibliotecas científicas y los
paquetes Python mantienen los avisos distribuidos con cada runtime.
