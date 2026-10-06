from pathlib import Path

from hasshr.cases import (
    CaseEngine,
    CaseState,
    Confidence,
    Diagnostician,
    Finding,
    Hypothesis,
    delete_case_state,
    list_saved_cases,
    load_case_state,
    resume_case,
    save_case_state,
    undo_case,
    watch_case,
)
from hasshr.cases.fixtures import (
    broken_service_fixture,
    simulated_kernel_regression_fixture,
)
from hasshr.contracts import Context, Outcome, Reversibility
from hasshr.tools.registry import ToolRegistry


class DummyApprover:
    def __init__(self, answer=True):
        self.answer = answer
        self.asked = []

    def confirm(self, question: str) -> bool:
        self.asked.append(question)
        return self.answer


def test_case_state_serialization(tmp_path: Path):
    cs = CaseState(
        problem="Web service crashing on startup",
        success_test="curl -f http://localhost:8080",
    )
    cs.findings.append(Finding(statement="port in use", confidence=Confidence.CONFIRMED, evidence=["port 8080"]))
    cs.hypotheses.append(Hypothesis(change="kill conflicting process", rationale="free up port", reversibility=Reversibility.FULL))

    # Save
    path = save_case_state(cs, base_dir=tmp_path)
    assert path.exists()

    # List
    cases = list_saved_cases(base_dir=tmp_path)
    assert len(cases) == 1
    assert cases[0].id == cs.id
    assert cases[0].problem == cs.problem

    # Load
    loaded = load_case_state(cs.id, base_dir=tmp_path)
    assert loaded is not None
    assert loaded.id == cs.id
    assert loaded.problem == cs.problem
    assert len(loaded.findings) == 1
    assert loaded.findings[0].statement == "port in use"
    assert len(loaded.hypotheses) == 1

    # Delete
    assert delete_case_state(cs.id, base_dir=tmp_path) is True
    assert load_case_state(cs.id, base_dir=tmp_path) is None


def test_diagnostician(tmp_path: Path):
    reg = ToolRegistry()
    diag = Diagnostician(registry=reg)
    ctx = Context()

    findings, hypotheses = diag.diagnose("test problem", ctx)
    assert isinstance(findings, list)
    assert isinstance(hypotheses, list)

    # Test confidence attribution
    ranked = diag.rank_hypotheses(hypotheses, findings)
    assert isinstance(ranked, list)

    # Distro kernel comparison
    comp = diag.compare_kernels_with_distro("6.8.0-45-generic", "Ubuntu")
    assert "current_kernel" in comp


def test_engine_lifecycle(tmp_path: Path):
    reg = ToolRegistry()
    approver = DummyApprover(answer=True)
    engine = CaseEngine(registry=reg, approver=approver, base_dir=tmp_path)

    cs = engine.create_case("Demo problem: service failure", "echo ok")
    assert cs.problem == "Demo problem: service failure"
    assert cs.success_test == "echo ok"

    # Run case
    final_state = engine.run(cs)
    assert final_state.phase.value in ("report", "done")
    assert final_state.outcome in (Outcome.FIXED, Outcome.UNRESOLVED, Outcome.KNOWN_ISSUE)
    assert final_state.report_path is not None
    assert Path(final_state.report_path).exists()
    report_content = Path(final_state.report_path).read_text()
    assert "# Troubleshooting Case Report:" in report_content


def test_resume_case(tmp_path: Path):
    cs = CaseState(problem="Post reboot check", success_test="echo 1")
    cs.reboot_pending = True
    save_case_state(cs, base_dir=tmp_path)

    # Resume case
    resumed = resume_case(cs.id, base_dir=tmp_path)
    assert resumed is not None
    assert resumed.reboot_pending is False


def test_undo_case(tmp_path: Path):
    cs = CaseState(problem="Undo test", success_test="echo 1")
    save_case_state(cs, base_dir=tmp_path)

    result = undo_case(cs.id, base_dir=tmp_path)
    assert result is True


def test_watch_case():
    cs = CaseState(problem="Watch test")
    status = watch_case(cs)
    assert "upstream_status" in status


def test_fixtures(tmp_path: Path):
    # Test broken service fixture
    srv = broken_service_fixture(base_dir=tmp_path)
    assert srv["service_file"].exists()
    assert srv["config_file"].exists()
    assert srv["verify_cmd"] != ""

    # Test simulated kernel regression fixture
    kr = simulated_kernel_regression_fixture(base_dir=tmp_path)
    assert kr["bad_kernel"] != ""
    assert kr["good_kernel"] != ""
