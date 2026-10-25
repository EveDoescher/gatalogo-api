"""Scoped diagnostic VM access through one owned USB forward; no expression evaluation."""

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import re
from urllib.parse import urlparse

from websockets.asyncio.client import connect


class Vm:
    def __init__(self, socket):
        self.socket, self.counter, self.isolate = socket, 0, None

    async def rpc(self, method, **params):
        self.counter += 1
        ident = str(self.counter)
        await self.socket.send(
            json.dumps(
                {"jsonrpc": "2.0", "id": ident, "method": method, "params": params}
            )
        )
        async with asyncio.timeout(15):
            while True:
                reply = json.loads(await self.socket.recv())
                if reply.get("id") == ident:
                    if "error" in reply:
                        raise RuntimeError(f"Diagnostic VM method {method} rejected")
                    return reply["result"]

    async def obj(self, ident):
        return await self.rpc("getObject", isolateId=self.isolate, objectId=ident)

    async def library(self, uri):
        isolate = await self.rpc("getIsolate", isolateId=self.isolate)
        refs = [r for r in isolate["libraries"] if r["uri"] == uri]
        assert len(refs) == 1
        return await self.obj(refs[0]["id"])

    async def instance(self, uri, name):
        library = await self.library(uri)
        classes = [c for c in library["classes"] if c["name"] == name]
        assert len(classes) == 1
        result = await self.rpc(
            "getInstances", isolateId=self.isolate, objectId=classes[0]["id"], limit=10
        )
        assert len(result["instances"]) == 1
        return result["instances"][0]["id"]

    async def field(self, ident, name):
        obj = await self.obj(ident)
        matches = [
            f["value"] for f in obj.get("fields", []) if f["decl"]["name"] == name
        ]
        assert len(matches) == 1
        return matches[0]

    async def allocations(self, gc=False):
        profile = await self.rpc("getAllocationProfile", isolateId=self.isolate, gc=gc)
        wanted = {
            "_CatDetailsPageState",
            "_ConversationPageState",
            "_ChatPageState",
            "ConversationController",
            "_CapturePageState",
            "CatPreflightService",
            "Interpreter",
            "_HabitatPageState",
            "_MainNavigationState",
            "_Timer",
            "AnimationController",
            "_BroadcastSubscription",
        }
        return {
            "dateLastGC": profile.get("dateLastServiceGC"),
            "memoryUsage": profile.get("memoryUsage"),
            "classes": [
                {
                    "name": m["class"]["name"],
                    "instances": m.get("instancesCurrent"),
                    "bytes": m.get("bytesCurrent"),
                }
                for m in profile.get("members", [])
                if m["class"]["name"] in wanted
            ],
        }


@asynccontextmanager
async def session(android, source_log):
    logs = Path(source_log).read_text(encoding="utf-8")
    urls = re.findall(r"Dart VM service is listening on (http://[^\s]+)", logs)
    assert urls
    uri = urlparse(urls[-1].rstrip(".,"))
    assert uri.hostname == "127.0.0.1"
    port = android.run("forward", "tcp:0", "tcp:" + str(uri.port)).decode().strip()
    assert port.isdecimal()
    try:
        async with connect(
            f"ws://127.0.0.1:{port}{uri.path.rstrip('/')}/ws",
            proxy=None,
            open_timeout=5,
            close_timeout=2,
            max_size=16 * 1024 * 1024,
        ) as socket:
            vm = Vm(socket)
            root = await vm.rpc("getVM")
            isolates = [i for i in root["isolates"] if i["name"] == "main"]
            assert len(isolates) == 1
            vm.isolate = isolates[0]["id"]
            yield vm
    finally:
        android.run("forward", "--remove", "tcp:" + port)
