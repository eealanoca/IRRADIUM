# IRRADIUM

*Irradiation Modeling and Simulation*

**An Integrated Framework for Irradiator Modeling, Simulation and Optimization**

Initial public release **0.3.0-alpha.1** for Linux x86-64. The implemented workflow models gamma irradiation in a category IV panoramic wet-storage facility with suspended carriers. Categories I, II and III are previews and extension points, not implemented simulators.

[Español](README.es.md) · [Quick start / English](docs/quickstart/IRRADIUM-quick-start-en.pdf) · [Guía rápida / Español](docs/quickstart/IRRADIUM-guia-rapida-es.pdf)

## Install

Download the `.tar.gz` from **Releases**, extract it into a writable directory and run:

```bash
tar -xzf IRRADIUM-0.3.0-alpha.1-linux.tar.gz
cd IRRADIUM
./install.sh
```

No administrator privileges are required. Installation needs Internet, a compatible Linux system, Bash, curl, tar and a browser. Allow at least **20 GB of free disk space**, plus space for your studies. The installer downloads Python, Node, OpenMC, CAD dependencies and nuclear data into this directory. The installation directory is a fixed prefix; do not move installed Conda runtimes to another path.

Nuclear data are **not embedded in the tarball or Git repository**. The installer streams the official OpenMC ENDF/B-VII.1 archive, retains the gamma-compatible profile and verifies every retained HDF5 file against pinned SHA-256 hashes. The archive itself is not retained. OpenMC 0.15.3 needs neutron nuclide data to initialize material compositions even for photon-only transport; those compatibility files are intentionally included. Original external libraries are never pruned. See [data provenance and notices](DATA_NOTICES.md).

PHITS/EGS5 is optional, installed and licensed separately. Configure its path in `workspace/Configuracion/instalacion.json`. The application uses its local nuclear library.

Launch **IRRADIUM** from the applications menu or run `./Iniciar_IRRADIUM.sh`. Open <http://127.0.0.1:3001/>. The first-run guide explains the workflow; reopen it with **Getting started**. Select **English / Español** in the header. Preferences are saved in the local workspace.

## Teaching example

A complete teaching configuration inspired by the commercial **SVST-Co-60/B** irradiator is included: 68 positions, explicit coordinates and rotations, equivalent cylindrical sources, a 54-body medical-fluid kit with five materials, a water STEP and an air-reference STEP. Create an independent copy from the getting-started guide.

The [published study](https://www.irjet.net/archives/V2/i2/Irjet-v2i2196.pdf) describes two levels, four passes per level, tote dimensions and reference dwell time. **Coordinates, individual source distribution and mechanical rotations not published there are explicitly reconstructed assumptions.** This is not a manufacturer-endorsed digital twin or a dosimetry benchmark. Original provenance is in [the example manifest](app/examples/commercial_svst/provenance.json).

A new installation starts with the example. Existing cases and source inventories are never overwritten. Each case freezes its geometry, sources, materials, date and route. The illustrated guides explain absolute Z rotations, show the alignment of turns in the dose sum, and walk through the application. The measured 68-position case completed in 22 min 52 s with 680 million histories and 3.70% maximum accumulated relative error. The 2% target remains unmet for this run. See [verification results and CSV files](docs/quickstart/results/).

## Workflow

1. Import a STEP containing the load, and assign material composition and density.
2. Review sources, irradiation date, route centers and rotations.
3. Create an irradiation case with an independent configuration snapshot.
4. In **Results**, select the route or positions, occupancy, method, mesh and history budget. Start, pause or resume the study.
5. Inspect 3D maps, material masks, slices, isodoses, DUR and relative errors.
6. Integrate a selected interval. Dose is `sum(rate_i × time_i)` in consistent units; solve for dwell time or total time using a chosen target-dose metric. Rotation alignment is applied to superposition, while individual views retain real irradiator axes.

Fast OpenMC flux conversion is recommended for air-reference comparisons. Intensive OpenMC and PHITS energy deposition account for material transport. Neighboring-load attenuation is an explicit occupancy option. A history budget does not guarantee a relative-error target, and Monte Carlo statistical uncertainty is not total model uncertainty.

## Files and uninstall

```text
IRRADIUM/
  install.sh, uninstall.sh          installation and removal entry points
  app/, scripts/                   source and lifecycle tools
  runtime/                         private Python, Node and CAD environments
  nuclear-data/                    verified data downloaded at installation
  docs/quickstart/                  English and Spanish guides and screenshots
  workspace/Configuracion/         installation, language and appearance settings
  workspace/irradiators/IV/
    Configuracion/                 editable category configuration
    Casos/<case_id>/               inputs, studies, maps and dose/time scenarios
  .local/                          caches and service state
```

Run `./uninstall.sh`. It defaults to **keeping studies and configurations** and asks for confirmation. Deleting data requires a stronger confirmation. It stops only verified services belonging to this installation and removes its menu link. External PHITS and other applications are not removed. Unknown user files and development Git history are preserved. OS/browser history and user-created copies are outside its scope. For a preview: `./uninstall.sh --dry-run`.

To reuse kept data, reinstall into the same directory. Historical studies may contain absolute paths.

## Development and verification

```bash
./irradium stop
./irradium exec npm --prefix apps/web run build
./irradium start
./irradium exec python -m unittest discover -s apps/api/tests -v
./irradium exec python -m unittest discover -s packaging/tests -v
./irradium exec python ../scripts/verify_self_contained.py
./irradium exec python ../scripts/verify_cad.py
```

The Git repository contains source, original examples and documentation, not private studies, installed runtimes or nuclear libraries. Use the **release installer** for installation: it also includes the local Micromamba and Bubblewrap bootstrap tools. Maintainers build the tarball with `./scripts/package.sh` from a prepared checkout. Do not redistribute PHITS or copyrighted manufacturer documents.

## License and scope

Original code and documentation: [MIT](LICENSE), preserving the original SIM Gamma copyright. Dependency licenses and data provenance remain separate; see [third-party notices](app/THIRD_PARTY_NOTICES.md) and [data notices](DATA_NOTICES.md).

This is an initial research and teaching release. It does not implement shielding certification, interlocks or facility control, and it has not been validated as an operational release criterion for an irradiation plant. Further irradiator categories and validation datasets are planned.
