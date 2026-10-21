"""Capture the dependency closure from the actual Linux Python 3.12 runtime.

Run in the tested runtime image, not in a Windows virtualenv. Output is JSON for
review before updating versioned locks; no packages are installed by this tool.
"""

import argparse
import importlib.metadata as metadata
import json
import platform
from pathlib import Path
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def capture(requirements):
    if platform.system() != "Linux" or platform.python_version_tuple()[:2] != (
        "3",
        "12",
    ):
        raise RuntimeError("Run inside the Linux Python 3.12 image being locked")
    seeds = [
        Requirement(line)
        for line in requirements.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    seeds.extend([Requirement("torch"), Requirement("torchvision")])
    selected, missing, sources = {}, set(), {}
    pending = seeds[:]
    visited = set()
    while pending:
        required = pending.pop()
        name = canonicalize_name(required.name)
        key = (name, tuple(sorted(required.extras)))
        if key in visited:
            continue
        visited.add(key)
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            missing.add(name)
            continue
        if required.specifier and not required.specifier.contains(dist.version):
            raise RuntimeError(f"{name} {dist.version} does not satisfy {required}")
        selected[name] = dist.version
        source = dist.read_text("direct_url.json")
        if source:
            sources[name] = json.loads(source)
        for raw in dist.requires or []:
            dep = Requirement(raw)
            if dep.marker is None or any(
                dep.marker.evaluate({"extra": extra})
                for extra in ["", *required.extras]
            ):
                pending.append(dep)
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "packages": selected,
        "sources": sources,
        "missing": sorted(missing),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("requirements", type=Path)
    print(json.dumps(capture(parser.parse_args().requirements), sort_keys=True))
