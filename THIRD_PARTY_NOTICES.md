# Source components and data attribution

This repository bundles source snapshots so collaborators can obtain the complete
workflow without access to internal servers. Source repository URLs and commit
IDs are listed in `source-versions.json`.

- `harbor/` and `FeatureFactory/harbor/`: original Apache-2.0 LICENSE files retained.
- `FeatureFactory/software-agent-sdk/`: original MIT LICENSE retained.
- `belta/` and `FeatureFactory/`: supplied project source snapshots; no blanket
  replacement license is assigned to these components by this packaging release.
- Dataset patches, test snapshots, repository files, and the corresponding Docker
  environments retain their upstream attribution and applicable licenses.
  Repository origins are listed in `repositories.json`; bundled files are not
  relicensed under Harbor's license.

Changes made for this distribution: materialize required submodules as source
directories; remove the now-inapplicable nested Git submodule declaration; add
top-level documentation, sanitized presets, evaluation and migration helpers;
add an optional endpoint override to the evaluation runner. The benchmark task
files, scoring implementation, and original prompts are preserved.
