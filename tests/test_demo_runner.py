from agentguard.demo.runner import run_demo


def test_demo_runs_all_scenarios(tmp_path):
    summary = run_demo(tmp_path)

    assert summary["success"] is True
    assert len(summary["cases"]) == 2

    assert (tmp_path / "refund_good.html").exists()
    assert (tmp_path / "refund_bad.html").exists()
    assert (tmp_path / "refund_regression.html").exists()

    assert (tmp_path / "crm_good.html").exists()
    assert (tmp_path / "crm_bad.html").exists()
    assert (tmp_path / "crm_regression.html").exists()