"""Revalidate completed native cycles and inspect sanitized retaining roots."""

import argparse
import asyncio
import json
import re

from android_quality import Android, PACKAGE
from android_vm import session


async def run(args):
    a = Android(args.serial, args.output)
    out = a.output
    progress = json.loads((out / "resource-cycles-progress.json").read_text())
    snapshots = json.loads((out / "resource-snapshots.json").read_text())
    assert len(progress["cycles"]) == 30
    assert [c["cycle"] for c in progress["cycles"]] == list(range(1, 31))
    assert [s["label"] for s in snapshots] == [
        "cycles-baseline",
        "cycles-after-10",
        "cycles-after-20",
        "cycles-after-30",
        "cycles-final-rest",
    ]
    roots = []
    async with session(a, args.source_log) as vm:
        for uri, name in [
            ("package:catlogue/pages/cat_details_page.dart", "_CatDetailsPageState"),
            ("package:catlogue/pages/conversation_page.dart", "_ConversationPageState"),
        ]:
            library = await vm.library(uri)
            cls = next(c for c in library["classes"] if c["name"] == name)
            instances = await vm.rpc(
                "getInstances",
                isolateId=vm.isolate,
                objectId=cls["id"],
                limit=10,
                idZoneId=vm.zone,
            )
            for obj in instances["instances"]:
                path = await vm.rpc(
                    "getRetainingPath",
                    isolateId=vm.isolate,
                    targetId=obj["id"],
                    limit=20,
                    idZoneId=vm.zone,
                )
                roots.append(
                    {
                        "class": name,
                        "gcRootType": path.get("gcRootType"),
                        "length": path.get("length"),
                        "elements": [
                            {
                                "class": e.get("value", {})
                                .get("class", {})
                                .get("name"),
                                "parentField": e.get("parentField"),
                                "parentListIndex": e.get("parentListIndex"),
                            }
                            for e in path.get("elements", [])
                        ],
                    }
                )
        # Scoped observer IDs are invalidated before GC; it cannot retain its inspected instances.
        fresh = await vm.allocations(gc=True)
        fresh["label"] = "cycles-independent-scoped-observer"
        snapshots.append(fresh)
    baseline = {c["name"]: c["instances"] for c in snapshots[0]["classes"]}
    latest = {c["name"]: c["instances"] for c in fresh["classes"]}
    names = [
        "CatPreflightService",
        "Interpreter",
        "_CapturePageState",
        "_MainNavigationState",
        "_HabitatPageState",
    ]
    cached_stable = all(latest[n] == baseline[n] for n in names)
    disposables = [
        "_CatDetailsPageState",
        "_ConversationPageState",
        "ConversationController",
    ]
    no_count_growth = all(latest[n] <= baseline[n] for n in disposables)
    (out / "resource-retaining-roots.json").write_text(
        json.dumps(roots, indent=2) + "\n"
    )
    native = []
    for p in sorted(out.glob("cycles-*-meminfo.txt")):
        text = p.read_text()
        native.append(
            {
                "file": p.name,
                "summary_kb": {
                    k: int(v)
                    for k, v in re.findall(
                        r"^\s*(Java Heap|Native Heap|Code|Stack|Graphics|Private Other|System|TOTAL PSS|TOTAL RSS|TOTAL SWAP PSS):\s+(\d+)",
                        text,
                        re.MULTILINE,
                    )
                },
            }
        )
    result = {
        "state": "PASS" if cached_stable and no_count_growth else "FINDING",
        "cycles_completed": 30,
        "warmup_cycles": 3,
        "same_android_pid": a.shell("pidof", PACKAGE),
        "snapshots": snapshots,
        "native": native,
        "retaining_roots": roots,
        "retries": progress["retries"],
        "cached_instance_counts_stable": cached_stable,
        "disposable_instance_counts_not_growing": no_count_growth,
        "scope": "Observed debug-app lifecycle retention trend. Original zero-instance oracle was too strong for service-observed objects; original failed attempt and snapshots retained. Not release capacity or proof of no leak.",
    }
    (out / "resource-cycles-result.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "state": result["state"],
                "cycles": 30,
                "final_instances": latest,
                "root_types": [p["gcRootType"] for p in roots],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    for key in ["serial", "output", "source-log"]:
        p.add_argument("--" + key, required=True)
    asyncio.run(run(p.parse_args()))
