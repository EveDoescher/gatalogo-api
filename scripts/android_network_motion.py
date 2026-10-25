"""Controlled Android offline/recovery and OS motion preference evidence."""

import argparse
import asyncio
import json
from pathlib import Path
import time

from android_quality import Android, PACKAGE
from android_vm import session


async def run(args):
    a = Android(args.serial, args.output)
    out = a.output
    points = json.loads(Path(args.points).read_text())["tabs"]
    async with session(a, args.source_log) as vm:

        async def tab(name, expected):
            for _ in range(3):
                nav = await vm.instance(
                    "package:catlogue/app.dart", "_MainNavigationState"
                )
                if (await vm.field(nav, "currentIndex"))["valueAsString"] == str(
                    expected
                ):
                    return
                assert PACKAGE in a.shell("dumpsys window | grep mCurrentFocus")
                a.shell("input", "tap", *points[name])
                await asyncio.sleep(0.6)
            raise AssertionError("Requested root tab not reached")

        async def feed():
            state = await vm.instance(
                "package:catlogue/pages/social_page.dart", "_SocialPageState"
            )
            return {
                key: (await vm.field(state, key))
                for key in ("_currentTab", "_loading", "_error", "_feedGeneration")
            }

        await tab("Social", 1)
        current = await feed()
        if current["_currentTab"]["valueAsString"] == "1":
            data = a.capture("network-social-inbox")
            back = [n for n in data["controls"] if n["label"] in ("Voltar", "Back")]
            assert len(back) == 1, "Need observed inbox back control"
            b = back[0]["bounds"]
            a.shell("input", "tap", (b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
            await asyncio.sleep(0.7)
        assert (await feed())["_currentTab"]["valueAsString"] == "0"
        a.capture("network-social-confirmed")
        wifi = a.shell("settings", "get", "global", "wifi_on")
        mobile = a.shell("settings", "get", "global", "mobile_data")
        assert wifi in ("0", "1") and mobile in ("0", "1")
        observations = {"original": {"wifi": wifi, "mobile_data": mobile}}

        def refresh():
            a.shell("input", "swipe", "540", "650", "540", "1600", "700")

        async def settled():
            deadline = time.monotonic() + 45
            while True:
                state = await feed()
                if state["_loading"]["valueAsString"] == "false":
                    return {
                        k: {"kind": v.get("kind"), "value": v.get("valueAsString")}
                        for k, v in state.items()
                    }
                assert time.monotonic() < deadline, (
                    "Feed request did not settle in 45 s"
                )
                await asyncio.sleep(0.5)

        try:
            a.shell("svc", "wifi", "disable")
            if mobile == "1":
                a.shell("svc", "data", "disable")
            await asyncio.sleep(2)
            refresh()
            await asyncio.sleep(1)
            observations["offline"] = await settled()
            assert observations["offline"]["_error"]["kind"] != "Null"
            a.capture("network-social-offline-verified")
        finally:
            a.shell("svc", "wifi", "enable" if wifi == "1" else "disable")
            if mobile == "1":
                a.shell("svc", "data", "enable")
            observations["restored"] = {
                "wifi": a.shell("settings", "get", "global", "wifi_on"),
                "mobile_data": a.shell("settings", "get", "global", "mobile_data"),
            }
            (out / "network-progress.json").write_text(
                json.dumps(observations, indent=2) + "\n"
            )
        await asyncio.sleep(8)
        refresh()
        await asyncio.sleep(1)
        observations["recovered"] = await settled()
        assert observations["recovered"]["_error"]["kind"] == "Null"
        assert observations["restored"] == observations["original"]
        a.capture("network-social-recovered-verified")
        (out / "network-result.json").write_text(
            json.dumps({"state": "PASS", **observations}, indent=2) + "\n"
        )
        print("Verified Social offline error and recovery PASS", flush=True)

        async def motion_flags():
            ui = await vm.library("dart:ui")
            cls = next(c for c in ui["classes"] if c["name"] == "PlatformDispatcher")
            fields = (await vm.obj(cls["id"]))["fields"]
            ref = await vm.obj(
                next(f["id"] for f in fields if f["name"] == "_instance")
            )
            config = await vm.field(ref["staticValue"]["id"], "_configuration")
            features = await vm.field(config["id"], "accessibilityFeatures")
            feature_obj = await vm.obj(features["id"])
            habitat = await vm.instance(
                "package:catlogue/pages/habitat_page.dart", "_HabitatPageState"
            )
            game = await vm.field(habitat, "_game")
            reduced = await vm.field(game["id"], "reducedMotion")
            return {
                "feature_fields": {
                    f["decl"]["name"]: f["value"].get("valueAsString")
                    for f in feature_obj.get("fields", [])
                },
                "yard_reduced_motion": reduced.get("valueAsString"),
            }

        keys = [
            "window_animation_scale",
            "transition_animation_scale",
            "animator_duration_scale",
        ]
        original = {key: a.shell("settings", "get", "global", key) for key in keys}
        observations = {"original": original, "conditions": []}
        try:
            for label, settings in [
                ("original", original),
                ("disabled", {k: "0" for k in keys}),
            ]:
                for key, value in settings.items():
                    a.shell("settings", "put", "global", key, value)
                await asyncio.sleep(2)
                await tab("Quintal", 4)
                observations["conditions"].append(
                    {"label": label, "effective": await motion_flags()}
                )
                a.capture("motion-yard-" + label)
                assert PACKAGE in a.shell("dumpsys window | grep mCurrentFocus")
                remote = "/data/local/tmp/gatalogo-quality-motion-" + label + ".mp4"
                try:
                    a.shell(
                        "screenrecord",
                        "--time-limit",
                        "6",
                        "--bit-rate",
                        "1000000",
                        remote,
                    )
                    a.run("pull", remote, out / ("motion-" + label + ".mp4"))
                finally:
                    a.shell("rm", "-f", remote)
                await tab("Início", 0)
        finally:
            for key, value in original.items():
                a.shell("settings", "put", "global", key, value)
            observations["restored"] = {
                key: a.shell("settings", "get", "global", key) for key in keys
            }
            (out / "motion-result.json").write_text(
                json.dumps(observations, indent=2) + "\n"
            )
        assert observations["restored"] == original
        print("Motion paired recording and preference restoration complete", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for key in ("serial", "output", "source-log", "points"):
        parser.add_argument("--" + key, required=True)
    asyncio.run(run(parser.parse_args()))
