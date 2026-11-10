"""Verify the actual pytest selections, rather than just registered marker names."""

import os
from pathlib import Path
import subprocess
import sys


def test_execution_layers_partition_the_collected_suite():
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")

    def collect(layer=None):
        command = [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"]
        if layer:
            command += ["-m", layer]
        result = subprocess.run(command, cwd=root, env=env, capture_output=True,
                                text=True, encoding="utf-8", timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        return {line.replace("\\", "/") for line in result.stdout.splitlines()
                if line.startswith("tests") and "::" in line}

    all_tests = collect()
    unit, integration, docker = (collect(layer) for layer in ("unit", "integration", "docker"))
    assert unit and integration and docker
    assert not (unit & integration or unit & docker or integration & docker)
    assert all_tests == unit | integration | docker
    assert any("test_real_process_tool_call" in item for item in integration)
    assert any("test_good_agent_cli_allows" in item for item in integration)
    assert any("test_docker_runtime_rejects_local_policy" in item for item in unit)
    for required in ("test_docker_worker_receives_request", "test_docker_worker_protocol_roundtrip", "test_timeout_removes_container"):
        assert any(required in item for item in docker)


def test_unknown_markers_are_collection_errors(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = tmp_path / "test_invalid_marker.py"
    source.write_text("import pytest\n@pytest.mark.not_registered\ndef test_invalid(): pass\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "--noconftest",
         "-c", str(root / "pyproject.toml"), str(source)],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 2
    assert "not_registered" in result.stdout + result.stderr
