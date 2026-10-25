"""Capture Android UI evidence over an explicitly selected USB device."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
ADB = "C:/Android/Sdk/platform-tools/adb.exe"
PACKAGE = "com.example.catlogue"


class Android:
    def __init__(self, serial, output):
        self.serial, self.output = serial, Path(output).resolve()
        if not self.output.is_relative_to(ROOT / ".tmp"):
            raise ValueError("Keep device evidence private under API/.tmp")
        self.output.mkdir(parents=True, exist_ok=True)

    def run(self, *args):
        return subprocess.check_output(
            [ADB, "-s", self.serial, *map(str, args)],
            timeout=30,
            stderr=subprocess.PIPE,
        )

    def shell(self, *args):
        return self.run("shell", *args).decode("utf-8", errors="replace").strip()

    def capture(self, label):
        if not re.fullmatch(r"[a-z0-9-]+", label):
            raise ValueError("Use a safe unique evidence label")
        folder = self.output / label
        folder.mkdir(exist_ok=False)
        focus = self.shell("dumpsys", "window")
        current = next(
            (line.strip() for line in focus.splitlines() if "mCurrentFocus=" in line),
            "",
        )
        if PACKAGE not in current:
            raise RuntimeError("Target app is not foreground; no screenshot captured")
        remote = "/data/local/tmp/gatalogo-quality-" + label + ".xml"
        (folder / "screen-initial.png").write_bytes(
            self.run("exec-out", "screencap", "-p")
        )
        try:
            for attempt in range(2):
                result = self.shell("uiautomator", "dump", remote)
                (folder / f"uiautomator-{attempt}.txt").write_text(
                    result, encoding="utf-8"
                )
                try:
                    self.run("pull", remote, folder / "hierarchy.xml")
                    break
                except subprocess.CalledProcessError:
                    if attempt == 1:
                        raise
        finally:
            self.shell("rm", "-f", remote)
        (folder / "screen.png").write_bytes(self.run("exec-out", "screencap", "-p"))
        density = self.shell("wm", "density")
        dpr = int(re.findall(r"density: (\d+)", density)[-1]) / 160
        controls = []
        for node in ET.parse(folder / "hierarchy.xml").getroot().iter("node"):
            if node.get("clickable") != "true" and node.get("focusable") != "true":
                continue
            bounds = list(map(int, re.findall(r"\d+", node.get("bounds", ""))))
            if len(bounds) != 4:
                continue
            x1, y1, x2, y2 = bounds
            controls.append(
                {
                    "label": node.get("content-desc") or node.get("text"),
                    "class": node.get("class"),
                    "bounds": bounds,
                    "size_dp": [round((x2 - x1) / dpr, 2), round((y2 - y1) / dpr, 2)],
                    "enabled": node.get("enabled"),
                    "selected": node.get("selected"),
                }
            )
        metadata = {
            "package": PACKAGE,
            "focus": current,
            "wm_size": self.shell("wm", "size"),
            "wm_density": density,
            "font_scale": self.shell("settings", "get", "system", "font_scale"),
            "dpr": dpr,
            "controls": controls,
            "hashes": {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in folder.iterdir()
            },
        }
        (folder / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return metadata

    def tap(self, capture_label, exact_label):
        data = json.loads(
            (self.output / capture_label / "metadata.json").read_text(encoding="utf-8")
        )
        matches = [
            n
            for n in data["controls"]
            if n["label"] == exact_label and n["enabled"] == "true"
        ]
        if len(matches) != 1:
            raise ValueError("Expected one observed enabled control")
        x1, y1, x2, y2 = matches[0]["bounds"]
        self.shell("input", "tap", (x1 + x2) // 2, (y1 + y2) // 2)

    def select_tab(self, current, tab, label):
        for attempt in range(2):
            self.tap(current, tab)
            time.sleep(0.5)
            current = label if attempt == 0 else label + "-retry"
            data = self.capture(current)
            if any(
                n["label"] == tab
                and n["selected"] == "true"
                and n["class"].endswith("Button")
                for n in data["controls"]
            ):
                return current
        raise RuntimeError("Requested tab was not selected; inspect both captures")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--capture", required=True)
    args = parser.parse_args()
    result = Android(args.serial, args.output).capture(args.capture)
    print(
        json.dumps(
            {
                "capture": args.capture,
                "controls": [
                    {
                        "label": n["label"],
                        "bounds": n["bounds"],
                        "size_dp": n["size_dp"],
                    }
                    for n in result["controls"]
                    if n["class"].endswith("Button")
                ],
            }
        )
    )
