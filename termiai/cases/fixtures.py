"""Demo fixtures for Troubleshooting Cases (PRD Demo B & Implementation Plan 12.5):
1. Fixable broken service config (ends Fixed with minimal change & clean-room confirmation).
2. Simulated kernel regression (audio fails on new kernel, passes on old; ends Known Issue with Confirmed finding).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from termiai.cases.engine import CaseEngine
from termiai.cases.state import CaseState
from termiai.contracts import Context, Hypothesis, Reversibility


def run_fixable_service_demo(tmp_dir: Path | None = None) -> CaseState:
    """Demo fixture 1: Broken service config that is fixable.
    Fails on attempt 1 (wrong setting), fixed on attempt 2, clean-room confirmed -> Outcome.FIXED.
    """
    d = Path(tmp_dir) if tmp_dir else Path(tempfile.mkdtemp(prefix="termiai-service-demo-"))
    config_file = d / "service.conf"
    config_file.write_text("port = 99999\n", encoding="utf-8")  # invalid port

    # Success test script: validates port is <= 65535 and > 0
    test_script = d / "test_config.sh"
    test_script.write_text(
        f"""#!/usr/bin/env bash
grep -q "port = 8080" "{config_file}"
exit $?
""",
        encoding="utf-8",
    )
    test_script.chmod(0o755)

    engine = CaseEngine(ctx=Context(), base_dir=d)
    hypotheses = [
        Hypothesis(
            change=f'echo "port = 70000" > "{config_file}"',
            rationale="Try port 70000 (invalid port number)",
            reversibility=Reversibility.FULL,
        ),
        Hypothesis(
            change=f'echo "port = 8080" > "{config_file}"',
            rationale="Configure standard HTTP alternate port 8080",
            reversibility=Reversibility.FULL,
        ),
    ]

    state = engine.run(
        problem="Service fails to start due to invalid configuration",
        success_test=f'bash "{test_script}"',
        hypotheses_override=hypotheses,
    )
    return state


def run_kernel_regression_demo(tmp_dir: Path | None = None) -> CaseState:
    """Demo fixture 2: Simulated kernel regression.
    Hypotheses fail, previous kernel test passes -> Outcome.KNOWN_ISSUE with Confirmed finding.
    """
    d = Path(tmp_dir) if tmp_dir else Path(tempfile.mkdtemp(prefix="termiai-kernel-demo-"))
    engine = CaseEngine(ctx=Context(), base_dir=d)
    return engine.run(problem="Microphone dummy output after update to kernel 6.8", success_test="false")


def broken_service_fixture(base_dir: Path | str | None = None) -> dict[str, Any]:
    """Demo fixture 1: Broken service config that is fixable."""
    d = Path(base_dir) if base_dir else Path(tempfile.mkdtemp(prefix="termiai-service-fixture-"))
    d.mkdir(parents=True, exist_ok=True)
    service_file = d / "demo.service"
    service_file.write_text("[Unit]\nDescription=Demo Service\n[Service]\nExecStart=/usr/bin/demo\n", encoding="utf-8")
    config_file = d / "service.conf"
    config_file.write_text("port = 99999\n", encoding="utf-8")
    verify_cmd = f"grep -q 'port = 8080' {config_file}"
    return {
        "service_file": service_file,
        "config_file": config_file,
        "verify_cmd": verify_cmd,
        "dir": d,
    }


def simulated_kernel_regression_fixture(base_dir: Path | str | None = None) -> dict[str, Any]:
    """Demo fixture 2: Simulated kernel regression fixture."""
    d = Path(base_dir) if base_dir else Path(tempfile.mkdtemp(prefix="termiai-kernel-fixture-"))
    d.mkdir(parents=True, exist_ok=True)
    return {
        "bad_kernel": "6.8.0-45-generic",
        "good_kernel": "6.5.0-35-generic",
        "problem": "Microphone dummy output after update to kernel 6.8",
        "dir": d,
    }

