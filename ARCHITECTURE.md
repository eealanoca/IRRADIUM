# Architecture

IRRADIUM is a local Linux application with a React/Vinext/VTK.js interface and a FastAPI backend. The internal Python namespace remains `sim_gamma` for compatibility with the MIT-licensed predecessor.

## Isolation and installation

`install.sh` enters the local lifecycle launcher. Micromamba installs private `runtime/main` and `runtime/cad` prefixes. Python and Node are invoked directly; no external Conda environment is activated. Bubblewrap makes the host filesystem read-only, with application writes confined to this installation and private temporary storage. PHITS is an optional external executable.

`install_nuclear_data.py` reads the official OpenMC archive as an XZ tar stream, extracts only files in `nuclear_data_profile.json`, verifies every selected SHA-256 and publishes a local index atomically. No data are embedded in source or release archives. Installed data take precedence over legacy external paths. Neutron nuclide files are retained only for material initialization required by OpenMC 0.15.3.

## Persistence

`workspace/Configuracion/` stores installation, appearance, language and onboarding preferences. Category configuration and cases live below `workspace/irradiators/<category>/`. Each case snapshots its inputs; changing global settings never rewrites historical cases. Studies, positional maps and dose/time scenarios are stored within their owning case. Geometry caches are content-addressed; identical immutable assets may share hard links within a category.

The initial public release enables category IV. Other categories have illustrative previews and explicit backend rejection; adding a category requires a workflow implementation and validation, not only changing a flag.

## Calculation and results

Sources are finite cylinders defined by endpoints and a fixed 11 mm diameter in the present workflow. STEP solids retain material tags. Geometry preparation, transport and postprocessing are distinct job stages. Case-bound status and run IDs support pause, continuation and reload without resubmission.

Individual views preserve irradiator axes. Dose/rate superposition registers the same physical load points across rotated poses. Independent-run uncertainties are propagated in quadrature with dwell-time weights. Relative-error percentiles and the raw maximum are separate statistics; statistical uncertainty is not total model uncertainty.

## Teaching and languages

The original commercial teaching reconstruction has explicit provenance and does not replace an existing user's source inventory. First-run guidance is persisted independently of appearance settings and can be reopened. English UI terminology is centralized in `lib/i18n/`; localization preserves React text-node identity and input values. Number formatting follows the selected language. Original case data and files are not rewritten by changing the interface language.

## Removal

`uninstall.sh` validates this installation and active jobs before stopping only verified service processes. It removes the owned applications-menu link and explicit program entries without following external symlinks. The default retains the complete workspace and compatibility links; data deletion requires separate confirmation. Unknown files, development Git history, user-created copies and browser/OS history are outside its deletion scope.
