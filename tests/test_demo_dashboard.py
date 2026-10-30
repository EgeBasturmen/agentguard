from agentguard.demo.dashboard import export_demo_dashboard


def test_demo_dashboard_generates_index(tmp_path):
    summary = {
        "cases": [
            {
                "name": "refund",
                "good_passed": True,
                "bad_passed": False,
                "regression_detected": True,
            },
            {
                "name": "crm",
                "good_passed": True,
                "bad_passed": False,
                "regression_detected": True,
            },
        ]
    }

    path = export_demo_dashboard(summary, tmp_path)

    content = path.read_text(encoding="utf-8")

    assert path.name == "index.html"
    assert "AgentGuard" in content
    assert "Detected Regressions" in content
    assert "refund_regression.html" in content
    assert "crm_regression.html" in content
    assert "Open Regression Comparison" in content