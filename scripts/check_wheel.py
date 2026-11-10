"""Install a wheel with runtime-only deps and exercise it outside the checkout.

Run with a build-tool interpreter: python scripts/check_wheel.py dist
The fresh target venv does not inherit site packages or install development extras.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tarfile
import venv


def select_wheel(path: Path) -> Path:
    wheels = list(path.glob("agentguard-*.whl")) if path.is_dir() else [path]
    if len(wheels) != 1 or not wheels[0].is_file() or wheels[0].suffix != ".whl":
        raise ValueError("Supply exactly one built AgentGuard wheel (remove stale build outputs).")
    return wheels[0].resolve()


def check_wheel(wheel: Path, wheelhouse: Path | None = None, *, docker: bool = False) -> dict:
    wheel = select_wheel(wheel)
    # Source distributions ship runnable test support rather than dangling test imports.
    for source_archive in wheel.parent.glob("agentguard-*.tar.gz"):
        with tarfile.open(source_archive) as archive:
            names=archive.getnames()
            for support in ("tests/conftest.py","tests/phase3_service.py"):
                assert any(name.endswith('/'+support) for name in names), f"sdist misses {support}"
    with tempfile.TemporaryDirectory(prefix="agentguard-wheel-") as directory:
        work = Path(directory).resolve()
        environment = work / "runtime"
        venv.EnvBuilder(with_pip=False, system_site_packages=False).create(environment)
        scripts = environment / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        cli = scripts / ("agentguard.exe" if os.name == "nt" else "agentguard")
        env = dict(os.environ)
        for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
            env.pop(key, None)
        env["PYTHONNOUSERSITE"] = "1"

        def run(command, expected=0):
            result = subprocess.run(
                [str(item) for item in command], cwd=work, env=env,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
            )
            if result.returncode != expected:
                raise RuntimeError(
                    f"Expected exit {expected}, got {result.returncode}: {command}\n"
                    f"{result.stdout}\n{result.stderr}"
                )
            return result.stdout

        install = [sys.executable, "-m", "pip", "--python", python, "install", "--disable-pip-version-check"]
        if wheelhouse is not None:
            install += ["--no-index", "--find-links", wheelhouse.resolve()]
        run([*install, wheel])
        run([sys.executable, "-m", "pip", "--python", python, "check"])
        probe = r'''
import importlib
import importlib.metadata
import importlib.resources
import importlib.util
import json
from pathlib import Path
import sys
import yaml

root = Path(sys.prefix).resolve()
modules = ["agentguard", "agentguard.cli.main", "agentguard.runner.runner",
           "agentguard.contracts.models", "agentguard.core.manifest",
           "agentguard.reporting.models", "agentguard.reporting.comparison",
           "agentguard.orchestration.controller", "agentguard.execution.controlled_runtime",
           "agentguard.execution.agent_spec", "agentguard.sdk", "agentguard.integrations.plan",
           "agentguard.environments.adapters"]
for name in modules:
    location = Path(importlib.import_module(name).__file__).resolve()
    assert location.is_relative_to(root), (name, location, root)
for name in ("pytest", "pytest_asyncio", "build"):
    assert importlib.util.find_spec(name) is None, f"Development dependency leaked: {name}"
for name in ("controlled_worker.py", "worker_client.py"):
    worker_source = importlib.resources.files("agentguard.execution").joinpath(name).read_text(encoding="utf-8")
    assert worker_source.strip()
distribution = importlib.metadata.distribution("agentguard")
requirements = distribution.requires or []
assert any(item.lower().startswith("pyyaml") and "extra" not in item for item in requirements)
assert "# AgentGuard" in distribution.metadata.get_payload()
direct_url = distribution.read_text("direct_url.json")
assert not direct_url or not json.loads(direct_url).get("dir_info", {}).get("editable", False)
fixtures = importlib.resources.files("agentguard.demo.fixtures")
for name in ("refund_order.yaml", "crm_update.yaml"):
    assert yaml.safe_load(fixtures.joinpath(name).read_text(encoding="utf-8"))["name"]
print(json.dumps({"version": distribution.version, "python": sys.version.split()[0],
                  "fixture": str(fixtures.joinpath("refund_order.yaml"))}))
'''
        details = json.loads(run([python, "-I", "-c", probe]))
        scenario = details.pop("fixture")
        run([cli, "--help"])
        run([cli, "demo", "--output-dir", work / "demo"])
        good = "agentguard.demo.fixtures.refund_agent:GoodRefundAgent"
        bad = "agentguard.demo.fixtures.refund_agent:BadRefundAgent"
        for name, reference, expected in (("good", good, 0), ("bad", bad, 1)):
            output = run([
                cli, "test", "--trusted-local", "--agent", reference, "--scenario", scenario,
                "--json-report", work / f"{name}.json", "--html-report", work / f"{name}.html",
            ], expected=expected)
            assert ("ALLOW" if expected == 0 else "BLOCK") in output
            report = json.loads((work / f"{name}.json").read_text(encoding="utf-8"))
            assert report["verdict"] == ("PASS" if expected == 0 else "FAIL")
        comparison = work / "comparison.json"
        run([cli, "compare", work / "good.json", work / "bad.json",
             "--json-report", comparison, "--html-report", work / "comparison.html"], expected=1)
        decision = json.loads(comparison.read_text(encoding="utf-8"))
        assert decision["gate_decision"] == "BLOCK" and decision["passed"] is False
        assert "BLOCK" in (work / "comparison.html").read_text(encoding="utf-8")
        run([cli, "compare", work / "good.json", work / "good.json",
             "--html-report", work / "same.html"])
        run([cli, "regression", "--trusted-local", "--baseline", good, "--candidate", bad,
             "--scenario", scenario, "--json-report", work / "live.json"], expected=1)
        live = json.loads((work / "live.json").read_text(encoding="utf-8"))
        for field in ("gate_decision", "passed", "regression_detected", "comparison_status", "findings"):
            assert live[field] == decision[field], field
        invalid = work / "invalid.yaml"
        invalid.write_text("name: invalid\ntask: x\ninitial_state: {}\n", encoding="utf-8")
        error = run([cli, "test", "--trusted-local", "--agent", "never_import:Agent", "--scenario", invalid], expected=2)
        assert "Scenario error" in error
        if docker:
            source = work / "runtime_agent.py"
            source.write_text(
                "import asyncio, os\n"
                "class Agent:\n"
                "    async def run(self, task, sandbox):\n"
                "        assert os.geteuid() == 65534\n"
                "        await asyncio.sleep(0)\n"
                "        order = sandbox.get_order('ORD-100')\n"
                "        sandbox.refund_order('ORD-100', order['total'])\n"
                "        return 'runtime-only controlled'\n", encoding="utf-8")
            controlled_path = work / "controlled.json"
            run([cli, "test", "--agent", "runtime_agent:Agent", "--source-dir", work,
                 "--scenario", scenario, "--json-report", controlled_path])
            report = json.loads(controlled_path.read_text(encoding="utf-8"))
            evidence = report["result"]
            assert report["verdict"] == "PASS"
            assert evidence["execution_context"]["execution_mode"] == "controlled"
            assert evidence["worker_output"] == "runtime-only controlled"
            assert evidence["cleanup_status"] == "verified" and evidence["policy_status"] == "passed"
            assert evidence["audit"] and evidence["run_manifest"]["completed"]
            run([cli, "compare", controlled_path, controlled_path, "--html-report", work / "controlled.html"])
            details["controlled_docker"] = True
            source.write_text(source.read_text(encoding="utf-8") +
                "\nclass WorkspaceAgent:\n"
                "    async def run(self, task, sandbox):\n"
                "        sandbox.call('file.write', {'filename':'note.txt','content':'done'})\n"
                "        return 'phase3 integration'\n", encoding="utf-8")
            plan = dict(schema_version=1, scenario=dict(name="workspace",task="edit note",initial_state={"files":{"note.txt":"draft"}},
                expected_state=[{"path":r"files.note\.txt","value":"done"}]),
                agent=dict(transport="python",reference="runtime_agent:WorkspaceAgent",source_dir="."),
                environment=dict(kind="filesystem"),tools=[dict(connector="environment")],policy=dict(allowed_tools=["file.write"]))
            plan_path=work/"integration-plan.json"
            plan_path.write_text(json.dumps(plan),encoding="utf-8")
            integration_path=work/"integration.json"
            run([cli,"integration",plan_path,"--json-report",integration_path])
            integration=json.loads(integration_path.read_text(encoding="utf-8"))
            assert integration["verdict"]=="PASS" and integration["result"]["integration_status"]=="verified"
            assert integration["result"]["worker_output"]=="phase3 integration"
            assert integration["result"]["cleanup_status"]=="verified"
            run([cli,"compare",integration_path,integration_path,"--html-report",work/"integration.html"])
            details["phase3_integration"] = True
        details.update({"wheel": wheel.name, "runtime_only": True, "outside_checkout": True,
                        "console_cli": True, "verdict_parity": True, "status": "passed"})
        return details


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--wheelhouse", type=Path, help="Optional offline runtime wheel directory")
    parser.add_argument("--result", type=Path, help="Write verification evidence as JSON")
    parser.add_argument("--docker", action="store_true", help="Also execute a real controlled agent from the clean installed wheel")
    args = parser.parse_args()
    result = check_wheel(args.wheel, args.wheelhouse, docker=args.docker)
    if args.result:
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
