# Stage2 Base Image Assets

This directory contains Dockerfile assets exposed to the planner agent through a
run-local read-only view at `$HOME/base_images`.

The planner does not see this directory directly. Before each planner run,
FeatureFactory materializes a filtered view containing only the Dockerfiles that
match the active base image catalog payload:

- Default network profile uses the upstream Dockerfile variants.
- China network profile uses the `*-cn.Dockerfile` variants.

Python worker images are Ubuntu-based and include Miniconda, uv, compiler tools,
and common native/scientific build dependencies. GPU/CUDA support is intentionally
out of scope for FeatureFactory stage2.
