"""Inventory tracked source and flag review candidates without deleting files.

Usage: python scripts/audit_quality.py PATH --output docs/quality_inventory.md
Dart reachability is static: dynamically selected assets are deliberately excluded.
"""

from __future__ import annotations
import argparse
import ast
from collections import Counter, defaultdict
from pathlib import Path
import re
import subprocess


def inventory(root: Path) -> str:
    tracked = subprocess.check_output(
        [
            "git",
            "-C",
            str(root),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        text=True,
    ).splitlines()
    sources = {
        name: (root / name).read_text(encoding="utf-8-sig")
        for name in tracked
        if name.endswith((".py", ".dart")) and (root / name).is_file()
    }
    out = [
        "# Inventário estático de qualidade",
        "",
        "Gerado por `scripts/audit_quality.py` na API. Candidatos exigem revisão; ausência de referência não prova inutilidade.",
        "",
        f"Arquivos versionados ou novos (respeitando .gitignore): {len(tracked)}. Fontes Python/Dart: {len(sources)}.",
        "",
        "## Distribuição das fontes",
        "",
        "| Diretório | Arquivos | Linhas |",
        "|---|---:|---:|",
    ]
    counts = Counter()
    lines = Counter()
    for name, source in sources.items():
        directory = str(Path(name).parent).replace("\\", "/")
        counts[directory] += 1
        lines[directory] += len(source.splitlines())
    out += [f"| {name} | {counts[name]} | {lines[name]} |" for name in sorted(counts)]
    out += [
        "",
        "## Fontes com mais de 500 linhas",
        "",
        "| Arquivo | Linhas |",
        "|---|---:|",
    ]
    out += [
        f"| `{name}` | {len(source.splitlines())} |"
        for name, source in sorted(
            sources.items(), key=lambda item: len(item[1].splitlines()), reverse=True
        )
        if len(source.splitlines()) > 500
    ]
    out += ["", "## Dependências entre rotas Python", ""]
    duplicates = defaultdict(list)
    for name, source in sources.items():
        if not name.startswith("app/") or not name.endswith(".py"):
            continue
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if (
                name.startswith("app/routers/")
                and isinstance(node, ast.ImportFrom)
                and (node.module or "").startswith("app.routers")
            ):
                out.append(f"- `{name}:{node.lineno}` → `{node.module}`")
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.end_lineno - node.lineno >= 4
            ):
                key = ast.dump(
                    ast.Module(body=node.body, type_ignores=[]),
                    include_attributes=False,
                )
                duplicates[key].append(f"{name}:{node.lineno} ({node.name})")
    out += ["", "## Corpos de funções Python idênticos", ""]
    out += ["- " + "; ".join(items) for items in duplicates.values() if len(items) > 1]
    dart = {name for name in sources if name.endswith(".dart")}
    graph = defaultdict(set)
    for name in dart:
        for match in re.finditer(
            r"""(?:import|export|part)\s+['"]([^'"]+)['"]""", sources[name]
        ):
            target = match.group(1)
            if target.startswith("package:catlogue/"):
                resolved = root / "lib" / target.removeprefix("package:catlogue/")
            elif ":" not in target:
                resolved = root / Path(name).parent / target
            else:
                continue
            try:
                relative = resolved.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                continue
            graph[name].add(relative)
    reachable = set()
    todo = ["lib/main.dart"]
    while todo:
        current = todo.pop()
        if current in reachable:
            continue
        reachable.add(current)
        todo.extend(graph[current] - reachable)
    out += [
        "",
        "## Dart fora do grafo de lib/main.dart",
        "",
        "Inclui componentes acessíveis somente por testes ou ferramentas. Confirmar antes de remover.",
        "",
    ]
    for name in sorted(dart - reachable):
        if not name.startswith("lib/"):
            continue
        refs = sorted(parent for parent, children in graph.items() if name in children)
        out.append(
            f"- `{name}` — referências: "
            + (", ".join(f"`{ref}`" for ref in refs) or "nenhuma importação estática")
        )
    out += ["", "## Arquivos rastreados que exigem classificação", ""]
    out += [
        f"- `{name}`"
        for name in tracked
        if Path(name).suffix in {".log", ".patch", ".bak", ".tmp", ".zip"}
        or "backup" in name.lower()
    ]
    out += [
        "",
        "## Inventário completo das fontes",
        "",
        "| Arquivo | Linhas |",
        "|---|---:|",
    ]
    out += [
        f"| `{name}` | {len(source.splitlines())} |"
        for name, source in sorted(sources.items())
    ]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(inventory(args.root), encoding="utf-8")
