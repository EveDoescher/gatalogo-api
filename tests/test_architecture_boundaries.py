"""Enforce the dependency and transaction boundaries established by the cleanup."""

import ast
import re
from pathlib import Path
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


def test_application_services_do_not_import_http_routers():
    for source in (ROOT / "app/services").rglob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("app.routers"), source
            elif isinstance(node, ast.Import):
                assert not any(
                    alias.name.startswith("app.routers") for alias in node.names
                ), source


def test_production_domain_handlers_cannot_commit_the_shared_transaction():
    for source in (ROOT / "app/services/quintal_commands").glob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"commit", "rollback"}, source


def test_runtime_requirements_are_pinned_and_cover_the_declared_inputs():
    pins = {}
    for source in (ROOT / "requirements").glob("*.txt"):
        for line in source.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith(("#", "--")):
                continue
            requirement = Requirement(line)
            pins[requirement.name.lower().replace("_", "-")] = requirement
            if requirement.url:
                assert re.search(r"@[a-f0-9]{40}$", requirement.url), line
            else:
                assert str(requirement.specifier).startswith("==") and "*" not in str(
                    requirement.specifier
                ), line
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        requirement = Requirement(line)
        locked = pins[requirement.name.lower().replace("_", "-")]
        if requirement.url:
            assert requirement.url == locked.url
        else:
            assert requirement.specifier.contains(next(iter(locked.specifier)).version)
    for name in ("Dockerfile", "Dockerfile.vision", "Dockerfile.postgres"):
        base = next(
            line
            for line in (ROOT / name).read_text(encoding="utf-8").splitlines()
            if line.startswith("FROM ")
        )
        assert re.search(r"@sha256:[a-f0-9]{64}", base), name
