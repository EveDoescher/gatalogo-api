"""Thirty real-device camera-cancel/details/chat/yard cycles with VM heap evidence."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import time

from android_quality import Android, PACKAGE
from android_vm import session


async def run(args):
    android = Android(args.serial, args.output)
    folder = android.output
    manifest = folder / "resource-cycles-manifest.json"
    assert not manifest.exists()
    manifest.write_text(
        json.dumps(
            {
                "cycles": 30,
                "warmup_cycles": 3,
                "executor_sha256": hashlib.sha256(
                    Path(__file__).read_bytes()
                ).hexdigest(),
                "scope": "Debug Android, existing development account/data. No message send, photo capture/upload, APK rebuild or app-data clearing. Camera launch is cancelled; only app UI is captured.",
            },
            indent=2,
        )
        + "\n"
    )
    reused = (
        json.loads(Path(args.reuse_points).read_text()) if args.reuse_points else None
    )
    initial = None if reused else android.capture("cycles-start")
    labels = ["Início", "Social", "Gatalogar", "Coleção", "Quintal"]
    points = {}
    for label in [] if reused else labels:
        node = next(
            n
            for n in initial["controls"]
            if n["label"] == label and n["class"].endswith("Button")
        )
        x1, y1, x2, y2 = node["bounds"]
        points[label] = ((x1 + x2) // 2, (y1 + y2) // 2)
    if reused:
        points = reused["tabs"]
    snapshots, cycles, retries = [], [], []
    camera_external = False
    async with session(android, args.source_log) as vm:

        async def index():
            nav = await vm.instance("package:catlogue/app.dart", "_MainNavigationState")
            return int((await vm.field(nav, "currentIndex"))["valueAsString"])

        def focus():
            return android.shell("dumpsys window | grep mCurrentFocus")

        async def tab(label):
            expected = labels.index(label)
            for attempt in range(3):
                if await index() == expected:
                    return
                assert PACKAGE in focus(), "Target app left foreground; input stopped"
                android.shell("input", "tap", *points[label])
                await asyncio.sleep(0.3)
                if attempt:
                    retries.append({"target": label, "attempt": attempt + 1})
            assert await index() == expected, "Navigation did not reach requested tab"

        def point(node, photo=False):
            x1, y1, x2, y2 = node["bounds"]
            assert x2 > x1 and y2 > y1
            return (
                (x1 + x2) // 2,
                int(y1 + (y2 - y1) * 0.25) if photo else (y1 + y2) // 2,
            )

        async def checkpoint(label):
            await asyncio.sleep(2)
            data = await vm.allocations(gc=True)
            data["label"] = label
            native = android.shell("dumpsys", "meminfo", PACKAGE)
            (folder / f"{label}-meminfo.txt").write_text(native, encoding="utf-8")
            data["app_cache_du"] = android.shell(
                "run-as", PACKAGE, "du", "-sk", "cache"
            )
            snapshots.append(data)
            (folder / "resource-snapshots.json").write_text(
                json.dumps(snapshots, indent=2) + "\n"
            )
            return data

        if reused:
            camera_point, cat_point, chat_point = (
                reused[k] for k in ("camera", "details", "chat")
            )
        else:
            await tab("Gatalogar")
            camera = android.capture("cycles-camera-setup")
            candidates = [
                n
                for n in camera["controls"]
                if n["label"] == "Abrir câmera" and n["bounds"][3] > n["bounds"][1]
            ]
            if not candidates:
                android.shell("input", "swipe", "540", "1750", "540", "750", "500")
                camera = android.capture("cycles-camera-scrolled")
                candidates = [
                    n
                    for n in camera["controls"]
                    if n["label"] == "Abrir câmera" and n["bounds"][3] > n["bounds"][1]
                ]
            assert len(candidates) == 1
            camera_point = point(candidates[0])
            capture_state = await vm.instance(
                "package:catlogue/pages/capture_page.dart", "_CapturePageState"
            )
            capture_state = await vm.instance(
                "package:catlogue/pages/capture_page.dart", "_CapturePageState"
            )
            assert (await vm.field(capture_state, "_foto"))["kind"] == "Null"

            await tab("Coleção")
            collection = android.capture("cycles-collection-setup")
            cat = next(
                n
                for n in collection["controls"]
                if "Gato #" in n["label"] and n["bounds"][1] > 400
            )
            cat_point = point(cat, photo=True)
            await tab("Social")
            inbox = android.capture("cycles-inbox-setup")
            peer = next(
                n for n in inbox["controls"] if n["label"].startswith("@evedoescher2")
            )
            chat_point = point(peer)
        (folder / "resource-navigation-points.json").write_text(
            json.dumps(
                {
                    "tabs": points,
                    "camera": camera_point,
                    "details": cat_point,
                    "chat": chat_point,
                    "peer": "provided development test account",
                },
                indent=2,
            )
            + "\n"
        )

        async def cycle(number):
            nonlocal camera_external
            tick = time.monotonic()
            await tab("Gatalogar")
            android.shell("input", "tap", *camera_point)
            external = focus()
            deadline = time.monotonic() + 8
            while (
                not ("camera" in external.lower() or "ResolverActivity" in external)
                and time.monotonic() < deadline
            ):
                await asyncio.sleep(0.3)
                external = focus()
            if PACKAGE not in external and "camera" in external.lower():
                camera_external = True
            (folder / "camera-focus-last.txt").write_text(external, encoding="utf-8")
            assert PACKAGE not in external and (
                "camera" in external.lower() or "ResolverActivity" in external
            ), (
                "Expected camera/chooser; no permission or other UI was accepted automatically"
            )
            camera_external = True
            android.shell("input", "keyevent", "4")
            for _ in range(15):
                if PACKAGE in focus():
                    break
                await asyncio.sleep(0.2)
            assert PACKAGE in focus()
            camera_external = False
            capture_state = await vm.instance(
                "package:catlogue/pages/capture_page.dart", "_CapturePageState"
            )
            assert (await vm.field(capture_state, "_foto"))["kind"] == "Null"
            await tab("Coleção")
            android.shell("input", "tap", *cat_point)
            await asyncio.sleep(0.4)
            await vm.allocations(gc=True)
            details = await vm.instance(
                "package:catlogue/pages/cat_details_page.dart", "_CatDetailsPageState"
            )
            assert details
            android.shell("input", "keyevent", "4")
            await asyncio.sleep(0.4)
            assert PACKAGE in focus()
            await tab("Social")
            android.shell("input", "tap", *chat_point)
            await asyncio.sleep(0.4)
            await vm.allocations(gc=True)
            page = await vm.instance(
                "package:catlogue/pages/conversation_page.dart",
                "_ConversationPageState",
            )
            controller = await vm.field(page, "_conversation")
            deadline = time.monotonic() + 15
            while (await vm.field(controller["id"], "_loading"))[
                "valueAsString"
            ] == "true":
                assert time.monotonic() < deadline, "History did not finish loading"
                await asyncio.sleep(0.25)
            android.shell("input", "keyevent", "4")
            await asyncio.sleep(0.4)
            assert PACKAGE in focus()
            await tab("Quintal")
            await asyncio.sleep(0.4)
            await tab("Início")
            return {
                "cycle": number,
                "elapsed_ms_observed": (time.monotonic() - tick) * 1000,
                "camera_cancelled": True,
                "details_opened_closed": True,
                "chat_history_loaded_and_closed": True,
                "yard_entered_left": True,
            }

        try:
            for n in range(3):
                await cycle("warmup-" + str(n + 1))
            await checkpoint("cycles-baseline")
            for n in range(1, 31):
                cycles.append(await cycle(n))
                (folder / "resource-cycles-progress.json").write_text(
                    json.dumps({"cycles": cycles, "retries": retries}, indent=2) + "\n"
                )
                if n % 10 == 0:
                    await checkpoint("cycles-after-" + str(n))
                    print(f"Android resource cycles: {n}/30", flush=True)
            await asyncio.sleep(3)
            final = await checkpoint("cycles-final-rest")
            by_name = {c["name"]: c["instances"] for c in final["classes"]}
            assert (
                by_name["_CatDetailsPageState"] == 0
                and by_name["_ConversationPageState"] == 0
                and by_name["ConversationController"] == 0
            )
            baseline = {c["name"]: c["instances"] for c in snapshots[0]["classes"]}
            for name in [
                "CatPreflightService",
                "Interpreter",
                "_CapturePageState",
                "_MainNavigationState",
                "_HabitatPageState",
            ]:
                assert by_name[name] == baseline[name], f"Retained {name} count changed"
            (folder / "resource-cycles-result.json").write_text(
                json.dumps(
                    {
                        "state": "PASS",
                        "cycles_completed": 30,
                        "same_android_pid": android.shell("pidof", PACKAGE),
                        "snapshots": snapshots,
                        "retries": retries,
                        "scope": "Observed debug-app lifecycle retention only; no release latency or universal no-leak claim",
                    },
                    indent=2,
                )
                + "\n"
            )
        finally:
            if camera_external:
                android.shell("input", "keyevent", "4")
    print("Thirty native lifecycle cycles complete; app data retained", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--source-log", required=True)
    parser.add_argument("--reuse-points")
    asyncio.run(run(parser.parse_args()))
