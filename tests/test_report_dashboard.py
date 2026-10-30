from agentguard.reporting.dashboard import export_comparison_html


def test_dashboard_exports_and_escapes_html(tmp_path):
    comparison = {
        "scenario": "refund-order",
        "baseline_agent": "GoodAgent",
        "candidate_agent": "<BadAgent>",
        "baseline_verdict": "PASS",
        "candidate_verdict": "FAIL",
        "regression_detected": True,
        "assertion_regressions": [
            {
                "name": "customer-unchanged",
                "message": "Customer deleted",
            }
        ],
        "added_tools": ["customer.delete"],
        "added_mutations": [
            {
                "path": "customers.CUST-1.status",
                "mutation_type": "updated",
                "before": "active",
                "after": "deleted",
            }
        ],
        "assertion_improvements": [],
        "baseline_metrics": {
            "assertions": 5,
            "mutations": 2,
            "traces": 6,
        },
        "candidate_metrics": {
            "assertions": 5,
            "mutations": 3,
            "traces": 8,
        },
    }

    path = export_comparison_html(
        comparison,
        tmp_path / "comparison.html",
    )

    content = path.read_text(encoding="utf-8")

    assert "REGRESSION DETECTED" in content
    assert "customer.delete" in content
    assert "customers.CUST-1.status" in content
    assert "&lt;BadAgent&gt;" in content
    assert "<BadAgent>" not in content