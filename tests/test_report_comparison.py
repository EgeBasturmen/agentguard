from agentguard.reporting.comparison import compare_reports


def test_detects_behavioral_regression():
    baseline = {
        "agent": "GoodRefundAgent",
        "verdict": "PASS",
        "result": {
            "scenario_name": "refund-order",
            "assertions": [
                {"name": "customer-unchanged", "passed": True}
            ],
            "mutations": [],
            "traces": [],
        },
    }

    candidate = {
        "agent": "BadRefundAgent",
        "verdict": "FAIL",
        "result": {
            "scenario_name": "refund-order",
            "assertions": [
                {
                    "name": "customer-unchanged",
                    "passed": False,
                    "message": "Customer unexpectedly deleted",
                }
            ],
            "mutations": [
                {
                    "path": "customers.CUST-1.status",
                    "mutation_type": "updated",
                    "before": "active",
                    "after": "deleted",
                }
            ],
            "traces": [
                {
                    "event_type": "tool_started",
                    "tool_name": "customer.delete",
                }
            ],
        },
    }

    result = compare_reports(baseline, candidate)

    assert result["regression_detected"] is True
    assert len(result["assertion_regressions"]) == 1
    assert "customer.delete" in result["added_tools"]
    assert len(result["added_mutations"]) == 1


def test_rejects_different_scenarios():
    baseline = {
        "agent": "A",
        "verdict": "PASS",
        "result": {
            "scenario_name": "refund",
        },
    }

    candidate = {
        "agent": "B",
        "verdict": "PASS",
        "result": {
            "scenario_name": "crm",
        },
    }

    try:
        compare_reports(baseline, candidate)
        assert False, "Expected ValueError"
    except ValueError:
        pass