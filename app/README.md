# IRRADIUM application core

<img src="icons/icon-512.png" alt="IRRADIUM" width="128">

The core contains the FastAPI service, React/Vinext frontend, transport adapters and CAD processing workers. The Python namespace `sim_gamma` is retained for compatibility with the MIT-licensed predecessor.

Start with the [installation and workflow guide](../README.md), [Spanish documentation](../README.es.md), or the [bilingual illustrated guides](../docs/quickstart/).

Use the root `install.sh` from the release archive. Dependencies and downloaded nuclear data remain in the installation directory; PHITS is external and optional. The initial public release implements category IV. The commercial example is a documented teaching reconstruction, not a validated industrial facility model.

See [third-party notices](THIRD_PARTY_NOTICES.md) and the root [MIT license](../LICENSE).
