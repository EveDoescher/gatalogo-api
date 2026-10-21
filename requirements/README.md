# Linux runtime locks

The three lock files pin the complete Python dependency closure for Linux amd64
and Python 3.12. They were captured from the working Linux worker image with
`scripts/export_runtime_lock.py`. The missing timezone wheel was pinned to the
installed application version, tzdata 2026.4, and is validated by the Linux build.
Test-only and Windows-only dependencies are excluded. SAM-2 uses its full source
commit, and Torch uses the official CPU index. Both Dockerfiles run pip check.

Both current images use CPU Torch. Dockerfile.vision targets the existing GT 610
host, without current CUDA support. There is no CUDA image profile to freeze;
a future CUDA/SAM 3 profile needs its own lock and runtime validation. The CPU
lock must not be reused for CUDA, and a Windows freeze must not be used for Linux.

The Python image is pinned by OCI digest. System packages still receive repository
security updates; these locks do not claim bit-for-bit OS images.

To update: run the exporter in the candidate Linux image against requirements.txt,
review changes, then build both Dockerfiles and run pip check plus application
checks. SAM-2 builds without isolation against the already pinned Torch.
