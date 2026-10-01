import pytest

from scripts.verify_phoenix import verify


def test_persisted_trace_identity_and_tree_are_required():
    root = {
        "name": "plan",
        "context": {"span_id": "root", "trace_id": "trace"},
        "parent_id": None,
        "attributes": {"wandergent.implementation.sha256": "version"},
        "status_code": "OK",
    }
    child = {
        "name": "graph.validate",
        "context": {"span_id": "child", "trace_id": "trace"},
        "parent_id": "root",
        "attributes": {"error.type": "ValueError"},
    }
    assert verify([root, child], "trace", {"graph.validate"})["persisted_spans"] == 2
    assert verify([root, child], "trace", {"plan"})["failures"] == ["ValueError"]
    with pytest.raises(ValueError, match="Expected spans"):
        verify([root], "trace", {"graph.validate"})
    with pytest.raises(ValueError, match="identity"):
        verify([root, child], "different", {"plan"})
    child["parent_id"] = "missing"
    with pytest.raises(ValueError, match="Missing parent"):
        verify([root, child], "trace", {"plan"})
    with pytest.raises(ValueError, match="No persisted"):
        verify([], "trace", {"plan"})
