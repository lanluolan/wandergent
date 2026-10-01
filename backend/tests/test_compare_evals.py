from copy import deepcopy

from scripts.compare_evals import compare


def test_comparison_reports_regression_and_does_not_claim_causality():
    before = {
        "completed": True,
        "model": "model",
        "reports": [
            {
                "id": "case",
                "passed": True,
                "checks_run": 2,
                "failures": {},
                "seconds": 10,
                "usage": {"llm_calls": 2, "total_tokens": 100},
                "cost_usd": 0.1,
            }
        ],
    }
    after = deepcopy(before)
    after["reports"][0].update(passed=False, failures={"check": "failed"}, seconds=5)
    result = compare(before, after)
    assert result["delta"]["seconds"] == -5
    assert result["quality_regressed_cases"] == ["case"]
    assert result["uncontrolled_live_variance"]
    assert result["before"]["executed_tools"] is None
    assert "maps_enabled" in result["unverified_settings"]
    after["model"] = "other"
    assert compare(before, after)["setting_mismatches"] == ["model"]
