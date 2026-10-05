import json
from pathlib import Path

from termiai.contracts import Action, Context, Mode, Reversibility
from termiai.safety.classifier import classify
from termiai.safety.permissions import decide

CORPUS_PATH = Path(__file__).parent / "command_corpus.json"


def test_command_corpus_accuracy_and_blocked_guarantee():
    data = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    total = len(data["cases"])
    correct = 0
    blocked_failures = []

    for case in data["cases"]:
        expected = case["expected"]
        risk = classify(case["command"], Context())
        actual = risk.level.name
        if expected == actual:
            correct += 1
        elif expected == "BLOCKED":
            blocked_failures.append((case["command"], actual))

    assert not blocked_failures, f"BLOCKED commands misclassified: {blocked_failures}"
    accuracy = correct / total
    assert accuracy >= 0.95, f"Classifier accuracy {accuracy:.2%} is below 95%"


def test_permission_modes():
    ctx = Context()

    safe_risk = classify("ls -la", ctx)
    assert decide(safe_risk, Mode.AUTO, ctx, Reversibility.FULL).action == Action.ALLOW

    sens_risk = classify("sudo systemctl restart nginx", ctx)
    assert decide(sens_risk, Mode.AUTO, ctx, Reversibility.FULL).action == Action.ALLOW
    assert decide(sens_risk, Mode.AUTO, ctx, Reversibility.NONE).action == Action.ASK
    assert decide(sens_risk, Mode.ASK_SENSITIVE, ctx, Reversibility.FULL).action == Action.ASK

    blocked_risk = classify("rm -rf /", ctx)
    for mode in Mode:
        assert decide(blocked_risk, mode, ctx, Reversibility.NONE).action == Action.REFUSE

    assert decide(safe_risk, Mode.PLAN_ONLY, ctx, Reversibility.FULL).action == Action.PLAN
    assert decide(sens_risk, Mode.PLAN_ONLY, ctx, Reversibility.FULL).action == Action.PLAN
