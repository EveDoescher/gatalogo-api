"""Publish only verified campaign observations; retain failed attempts and history."""

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "app").is_dir())
RUN = ROOT / ".tmp/quality-campaign/20261021T194312Z-953aa74e"
SEQ = RUN / "sequential"
DOC = ROOT / "docs/campaigns/2026-10-23-sequential"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path):
    return path.relative_to(ROOT).as_posix()


def junit(path, green=True):
    tree = ET.parse(path)
    cases = list(tree.getroot().iter("testcase"))
    result = {
        "tests": len(cases),
        "failures": sum(c.find("failure") is not None for c in cases),
        "errors": sum(c.find("error") is not None for c in cases),
        "skips": sum(c.find("skipped") is not None for c in cases),
        "seconds": sum(float(c.get("time", 0)) for c in cases),
        "selectors": [c.get("classname", "") + "::" + c.get("name", "") for c in cases],
    }
    assert cases
    if green:
        assert not any(result[k] for k in ("failures", "errors", "skips")), path
    return result


def evidence(folder):
    return [{"private_path": relative(p), "sha256": sha(p)}
        for p in sorted(folder.rglob("*")) if p.is_file()
        and p.suffix in {".json", ".xml", ".log", ".py", ".diff"}]


def attempt(name):
    folder = SEQ / name
    result = read(folder / "summary.json")
    assert result["exit_code"] == 0 and result["evaluated_sources_unchanged"], name
    return {"attempt": name, "junit": junit(folder / "junit.xml"),
        "source": read(folder / "source_manifest.json"),
        "observations": {p.stem: read(p) for p in sorted((folder / "observations").glob("*.json"))},
        "evidence": evidence(folder)}


def publish(name, content):
    content.setdefault("date", "2026-10-27")
    content["report_executor"] = "scripts/campaign/publish_no_usb_20261027.py"
    content["report_executor_sha256"] = sha(Path(__file__))
    (DOC / name).write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def commit(subject):
    return subprocess.check_output(["git", "log", "-1", "--format=%H", "--grep=" + subject], cwd=ROOT, text=True).strip()


def main():
    archive_root = ROOT / "artifacts/reports/quality-campaign/2026-10-27-no-usb"
    archive_root.mkdir(parents=True, exist_ok=True)
    raw = sorted(p for folder in SEQ.iterdir() if folder.is_dir() and "20261027" in folder.name
        for p in folder.rglob("*") if p.is_file() and (
            p.suffix in {".json", ".jsonl", ".xml", ".log", ".py", ".dart", ".diff", ".md", ".csv", ".txt"}
            or p.name == "candidate.tar"))
    archive_manifest = [{"private_path": relative(p), "sha256": sha(p), "bytes": p.stat().st_size} for p in raw]
    digest = hashlib.sha256(json.dumps(archive_manifest, sort_keys=True).encode()).hexdigest()[:16]
    archive = archive_root / ("raw-" + digest + ".zip")
    if not archive.exists():
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as z:
            for p in raw:
                z.write(p, relative(p))
    assert all(sha(ROOT / item["private_path"]) == item["sha256"] for item in archive_manifest)
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None and set(z.namelist()) == {item["private_path"] for item in archive_manifest}
    archive_index = {"archive": relative(archive), "sha256": sha(archive), "files": archive_manifest,
        "scope": "Raw manifests, executors, JUnit, logs and observations for all 20261027 sequential attempts; originals remain untouched. Private configs may contain disposable JWTs; archive is local/ignored and is not pushed. Databases, build binaries and model weights are not copied."}
    (archive_root / ("manifest-" + digest + ".json")).write_text(json.dumps(archive_index, indent=2) + "\n", encoding="utf-8")
    publish("RAW-20261027-manifest.json", archive_index)
    delivery = attempt("DLV02-20261027-after-02")
    recovery = attempt("RES05-20261027-final")
    regressions = [attempt(name) for name in (
        "SEC07-20261027-otp-regression", "SEC01-20261027-delivery-storage-regression",
        "SEC05-20261027-cleanup-regression", "DATA02-20261027-cleanup-regression",
    )]
    corrections = {
        "otp": commit("persist OTP before SMTP delivery"),
        "cleanup": [commit("retry photo cleanup from a persistent"), commit("recover uncommitted photo files")],
        "dino": commit("cached DINO repository without branch"),
        "numpy": commit("serialize real detector coordinates"),
    }
    old_key = "attempts/storage-resilience-01/critical_runs/storage-resilience-baseline/observations/RSC03-postcommit-cleanup.json"
    old_observations = read(ROOT / "docs/campaigns/2026-10-23-local-campaign/observations.json")
    units = {"junit": junit(SEQ / "UNIT-20261027/junit.xml"), "evidence": evidence(SEQ / "UNIT-20261027"),
        "tested_changes_commit": corrections["numpy"]}
    publish("DLV02-RES05-20261027-results.json", {
        "scenarios": ["DLV-02", "RES-05"], "state": "COMPLETED", "date": "2026-10-27",
        "delivery": delivery, "recovery": recovery, "regressions": regressions,
        "corrections": corrections,
        "local_unit_regressions": units,
        "before": {"smtp": attempt("DLV02-20261027-before"),
            "storage": {"historical_observation": old_observations[old_key],
                "public_source": "../2026-10-23-local-campaign/observations.json#" + old_key,
                "historical_issue_id": "RSC-F-CLEANUP"},
            "scope": "Green characterization tests reproduce defects; they do not approve defective product behavior. Historical storage characterization is retained in the previous reports and the unchanged private campaign tree."},
        "cycle": [
            {"problem": "SMTP acceptance before DB commit can deliver an unusable OTP; ambiguous acceptance plus rollback permits duplicate immediate emails", "hypothesis": "Commit OTP and cooldown before SMTP", "fix_commit": corrections["otp"], "revalidation": "Commit failure sends zero messages; ambiguous acceptance preserves consumable code, immediate retry 429; real loopback greeting timeout ~15s then recovery 202 after cooldown"},
            {"problem": "Deleting an old photo after commit raises to the client and leaves an orphan; file writes before rollback can also remain", "hypothesis": "Persist candidates before writing/committing, then recheck committed references under Cat lock", "fix_commits": corrections["cleanup"], "revalidation": "Upload returns 204 during cleanup failure; a new cleanup worker recovers it; rollback preserves active file and removes uncommitted file; nowait lock defers concurrent cleanup; references/legacy separators preserved"},
        ],
        "limitations": [
            "Disposable PostgreSQL, files and Mailpit only. FCM uses real sender/persisted receipts with isolated HTTP transport: partial and total 503 recovery; no Google delivery or physical phone.",
            "SMTP timeout is per socket operation, not an end-to-end deadline. Commit-before-send does not provide a durable mail outbox, exactly-once SMTP, or delivery after process exit between commit/send; a failed send retains the 60s cooldown.",
            "DB pause covers 0.5s stall/recovery and actual ~10s client ReadTimeout followed by recovery. It does not certify an API-side SQL deadline or crash/power-loss durability.",
            "Cleanup journal targets the local private filesystem, with atomic candidate writes, up to 25 per sweep and 30s background interval. Failed candidates persist. This does not certify object stores, power loss or recovery of anonymous legacy temporary upload files.",
            "Functional checks and bounded durations are not latency gains or capacity measurements.",
        ],
    })
    mutations = []
    for name in ("MUT13-20261027-otp-order", "MUT14-20261027-photo-references"):
        folder = SEQ / name
        result = read(folder / "mutation.json")
        assert result["classification"] == "KILLED"
        mutations.append({"attempt": name, "result": result,
            "baseline": junit(folder / "baseline-junit.xml"),
            "mutant": junit(folder / "junit.xml", green=False), "evidence": evidence(folder)})
    publish("MUT13-MUT14-20261027-results.json", {
        "state": "COMPLETED", "mutants": mutations, "killed": 2, "survived": 0,
        "scope": "Two targeted mutants, each isolated and preceded by its own green baseline. No whole-product mutation score or unexecuted mutant claims.",
    })
    caps = attempt("RSC06-20261027-caps-03")
    conditions = []
    for condition in ("unrestricted", "cpu1-commit512MiB"):
        folder = SEQ / caps["attempt"] / "observations" / condition
        row = read(folder / "RSC06-resource-caps.json")
        latency = read(folder / "LAT08-controlled-tcp.json")
        assert sum(b["completed_once"] for b in row["reference_batches"]) == 122
        assert latency["passed_light_requests"] == 900 and latency["completed_controlled_analyses"] == 6
        conditions.append({"resources": row, "latency": latency})
    publish("RSC06-20261027-results.json", {
        "scenario": "RSC-06", "state": "COMPLETED", "run": caps, "conditions": conditions,
        "completed_reference_jobs": 244, "warmup_reference_jobs": 2,
        "measured_http_requests": 1800, "http_warmup_requests": 180,
        "controlled_api_analyses": 12, "paid_calls": 0,
        "limitations": ["One exploratory block per condition; fixed order, shared host, not an isolated/randomized causal experiment. No P99 or statistical speedup claim.",
            "100 HTTP samples per route/phase and ten warmups prioritize integrity under a bounded budget. Confirmatory latency needs larger, repeated controlled blocks.",
            "One logical CPU and 512 MiB private committed memory apply to the API/coordinator process after startup. Win32 query and rejected oversized allocation verify enforcement. This is not a 512 MiB RSS cap. Generator restores the original CPU mask but inherits the per-process memory ceiling; DB has no added cap.",
            "Reference batches use real worker claims/finishes and PG but controlled inference/progression; actual model resources are in RSC-01. Lifetime process peaks include preceding work and are not independent phase peaks."],
    })
    worker = attempt("RSC01-20261027-real-worker-04")
    lifespan = attempt("RSC01-BOOT-20261027-02")
    worker_parent = worker["observations"]["RSC01-fresh-processes"]
    worker["observations"] = {"roles": worker_parent["roles"], "processes": worker_parent["processes"],
        "weights_sha256": worker_parent["weights_sha256"], "sampling": worker_parent["sampling"]}
    api_folder = SEQ / "RSC01-20261027-real-models-02"
    api_source = read(api_folder / "source_manifest.json")
    changed = sorted(k for k, v in api_source["evaluated_content_sha256"].items()
        if k.startswith("app/") and worker["source"]["evaluated_content_sha256"][k] != v)
    assert changed == ["app/services/vision_worker.py"], changed
    trials = []
    for role, folder in (("api", api_folder), ("worker", SEQ / worker["attempt"])):
        for trial in range(2):
            child = folder / "observations" / f"{role}-{trial}"
            tests = junit(child / "junit.xml")
            row = read(child / "RSC01-local-models.json")
            row.pop("resource_trace")
            assert row["paid_calls"] == row["external_attempts_blocked"] == 0 and row["output_consistency"]
            assert len(row["executions"]) == 4
            trials.append({"role": role, "trial": trial, "result": row, "junit": tests, "evidence": evidence(child)})
    publish("RSC01-20261027-results.json", {
        "scenario": "RSC-01", "state": "COMPLETED", "trials": trials,
        "actual_lifespan_startup_shutdown": lifespan,
        "lifespan_timing_definition": "boot_seconds measures entering lifespan and scheduling its tasks, not complete server/model/database readiness. Four actual application service tasks were observed alive, HTTP/authenticated DB reads succeeded, and all four stopped with pool returned to zero. Fixture TCP accept loop is separately owned and intentionally remains until fixture teardown.",
        "worker_run": worker, "api_source": api_source,
        "api_children_reused_from_failed_parent_attempt": relative(api_folder),
        "reuse_justification": "Both API child JUnit runs passed. Parent failed in a separate worker fixture. The only app module difference is vision_worker.py, which the web path does not import. Its offline-loader defect is corrected and verified in the two new worker processes. Failed parent is not represented as a green full suite.",
        "api_to_worker_changed_app_modules": changed, "corrections": corrections,
        "dataset": {"fixture": "tests/fixtures/local_models/chelsea.png", "sha256": "596aa1e7cb875eb79f437e310381d26b338a81c2da23439704a73c4651e8c4bb", "source": "https://raw.githubusercontent.com/scikit-image/scikit-image/v0.18.3/skimage/data/chelsea.png", "license": "CC0, Stefan van der Walt", "license_source": "https://scikit-image.org/docs/stable/api/skimage.data.html#skimage.data.chelsea"},
        "cycle": {"problem": "An unqualified torch.hub repository triggers default-branch discovery even with cached code/weights", "fix_commit": corrections["dino"], "before": "RSC01-20261027-real-worker-03: network guard stopped the default-branch request", "after": "Two new worker processes: eight actual model jobs completed, no external attempt, unit-norm finite 384-dimensional vectors consistent for identical bytes", "qualification": "PyTorch has a URLError cache fallback; this does not claim that every offline environment previously failed. A named main ref avoids discovery, but is not an immutable supply-chain revision pin."},
        "limitations": ["Two fresh processes per role, one model-cold and three warm executions each, five seconds idle at 10Hz. Descriptive resource observations; no P99, population accuracy, confidence-bound speedup or leak certification.", "Faster R-CNN/SAM2 are real; DINOv2 is real in the persisted worker. API semantics are controlled and network blocked. No paid-provider latency, classifications or confirmed reidentification claim.", "PyTorch import is recorded separately before the first model-cold execution. OS filesystem cache was not cleared, and preflight SHA reads warm file cache. Existing 2 GiB per-process private-commit guard was applied to each child.", "Checkpoint hashes are in the successful worker preflight/result. API children used the same existing cached files and prohibited downloads; they were collected before the worker-only correction. Resource results are source-versioned historical baselines, not measurements of every later correction."],
    })
    closures = {"DLV-02": "DLV02-RES05-20261027-results.json", "RES-05": "DLV02-RES05-20261027-results.json",
        "RSC-06": "RSC06-20261027-results.json", "RSC-01": "RSC01-20261027-results.json"}
    image_folder = SEQ / "IMG05-20261027-real-rejection-02"
    if (image_folder / "summary.json").exists() and read(image_folder / "summary.json")["exit_code"] == 0:
        image = attempt(image_folder.name)
        publish("IMG05-20261027-results.json", {
            "scenario": "IMG-05", "state": "COMPLETED", "run": image,
            "fix_commit": corrections["numpy"],
            "before": "IMG05-20261027-real-rejection: multiple-cat detection reached cache json.dumps with NumPy int64 coordinates and raised TypeError; owned-cat reward assertion was a fixture error, corrected to preserve non-eligibility.",
            "after": "Native integer coordinates retain numeric values; actual multiple-cat input returns MULTIPLE_CATS on first/repeated calls. Rejections do not persist photos/analysis/jobs; accepted blur preserves owned-cat no-reward rule. Exact configured byte/pixel -1/0/+1 boundaries have positive controls.",
            "limitations": ["Single generated noise image and repeated CC0 two-copy composition are deterministic rejection fixtures, not detection accuracy or identity evaluation.", "Faster R-CNN/SAM2/quality are real; accepted blur semantics are controlled; no paid AI.", "Byte/pixel thresholds are deliberately reduced to bounded test limits. Pixel dimensions solve exact +/-1 while keeping both original sides >=128; actual production-limit capacity is not certified. Durations are contextual, not latency gains."],
        })
        closures["IMG-05"] = "IMG05-20261027-results.json"
    publish("ISSUES-20261027-results.json", {
        "date": "2026-10-27", "correction_commits": corrections,
        "resolved_local": [
            {"id": "DLV-F-COMMIT-ORDER", "description": "SMTP before OTP commit", "result": "Commit failure sends zero emails; uncertain delivery retains consumable code/cooldown", "report": "DLV02-RES05-20261027-results.json"},
            {"id": "RSC-F-CLEANUP", "description": "Postcommit deletion failure and uncommitted photo orphan", "result": "Persistent candidate reconciliation, reference/lock checks and worker restart validated", "report": "DLV02-RES05-20261027-results.json"},
            {"id": "RSC-F-DINO-DISCOVERY", "description": "Unnecessary GitHub branch lookup with local cache", "result": "Explicit same main ref; real queued inference completes with external networking forbidden", "report": "RSC01-20261027-results.json"},
            {"id": "IMG-F-NUMPY-BBOX", "description": "NumPy int64 prevents serializing multiple-cat cache", "result": "Coordinate values preserved as native ints; first/repeated actual rejection and cache unit regression pass", "report": "IMG05-20261027-results.json"},
        ],
        "remaining_recommendations": ["Durable SMTP outbox/exactly-once semantics are not implemented; SMTP acceptance and a DB transaction cannot be treated as atomic.", "Choose and validate API-side SQL deadlines; this campaign measured the client timeout and recovery, not a guaranteed server deadline.", "Power-loss/temp-file crash recovery and object-storage reconciliation need separate experiments.", "Dependency reachability/remediation remains in SEC-09; passing security tests is not clearance of all advisory findings.", "DINO main is a named ref, not a supply-chain commit pin. Preserve/check cached code and model digests for future experiments."],
        "historical_reports_unchanged": True,
    })
    matrix = read(DOC / "matrix.json")
    for row in matrix:
        if row["scenario"] in closures:
            report = closures[row["scenario"]]
            row["state"] = "COMPLETED"
            row["closure_evidence"] = list(dict.fromkeys([*row.get("closure_evidence", []), report]))
            row["limitation"] = "All planned variants executed for the documented local scope; exact sources, controls, findings and limitations are in " + report
            row["executor"] = "scripts/run_security_regression.py --scenario " + row["scenario"] + " --output <new private attempt>"
    assert len(matrix) == len({row["scenario"] for row in matrix}) == 95
    (DOC / "matrix.json").write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    table = (DOC / "matrix.md").read_text(encoding="utf-8")
    for scenario in closures:
        table, n = re.subn(r"\| " + re.escape(scenario) + r" \| [A-Z_]+ \|", "| " + scenario + " | COMPLETED |", table)
        assert n == 1
    (DOC / "matrix.md").write_text(table, encoding="utf-8")
    counts = dict(Counter(row["state"] for row in matrix))
    block = "\n<!-- no-usb-20261027:start -->\n## Atualização sem USB — 27/10/2026\n\n"
    block += f"**{counts['COMPLETED']} concluídos, {counts['PARTIAL_EXECUTED']} parciais, {counts['PENDING']} pendentes e {counts['BLOCKED_EXTERNAL_PREREQUISITES']} bloqueados externos; 95 cenários.**\n\n"
    block += "Concluídos nesta bateria: " + ", ".join(closures) + ". Nenhum aparelho, teste iOS ou chamada de IA paga. A main permanece preservada.\n\n"
    block += "- [SMTP, resiliência, correções e regressões](DLV02-RES05-20261027-results.json): seis casos SMTP e 25 de resiliência, além de autenticação, acesso privado e rollback.\n"
    block += "- [Dois mutantes detectados](MUT13-MUT14-20261027-results.json): ordem commit/envio e proteção de arquivos referenciados, com baselines verdes e cópias isoladas.\n"
    block += "- [Limites de CPU/memória](RSC06-20261027-results.json): 244 jobs e 1.800 requisições medidas; controle de um núcleo/512 MiB verificado pelo Windows; sem alegação de ganho.\n"
    block += "- [Modelos locais reais](RSC01-20261027-results.json): quatro processos, 16 execuções, detector/SAM2 e DINOv2, oito jobs persistidos e repouso; sem Gemini real ou identificação confirmada.\n"
    if "IMG-05" in closures:
        block += "- [Rejeições reais e bordas](IMG05-20261027-results.json): cache de múltiplos gatos corrigido sem mudar coordenadas/limiares; repetição e efeitos de negócio preservados.\n"
    block += "\n[Quatro problemas corrigidos e recomendações restantes](ISSUES-20261027-results.json). Os 55 testes locais também passaram. [Índice do arquivo bruto local](RAW-20261027-manifest.json): ZIP e manifesto em `artifacts/reports/quality-campaign/2026-10-27-no-usb/`, fora do Git; nenhum original foi removido.\n\nAs tentativas inválidas e os resultados anteriores foram preservados. Falhas de fixture não são resultados de desempenho. Fontes, SHAs, seletores, hashes das evidências, hipótese/correção e revalidação estão nos relatórios. SMTP não promete entrega exatamente uma vez; cliente com timeout não prova deadline SQL; amostras pequenas não sustentam P99.\n\n"
    block += "Fora desta execução continuam os percursos que exigem aparelho, entrega real FCM/credenciais externas, conjuntos rotulados independentes e variantes pagas de IA. LOAD-01/02/06 exigem carga prolongada, ainda excluída nesta etapa. BR-05 depende da comparação A/B completa. Modelos locais disponíveis e uma foto CC0 não substituem um conjunto de reidentificação.\n<!-- no-usb-20261027:end -->\n"
    readme = (DOC / "README.md").read_text(encoding="utf-8")
    if "<!-- no-usb-20261027:start -->" in readme:
        readme = re.sub(r"\n<!-- no-usb-20261027:start -->.*?<!-- no-usb-20261027:end -->\n", block, readme, flags=re.S)
    else:
        first, rest = readme.split("\n", 1)
        readme = first + "\n" + block + rest
    (DOC / "README.md").write_text(readme, encoding="utf-8")
    print(json.dumps({"closed": closures, "counts": counts}))


if __name__ == "__main__":
    main()
