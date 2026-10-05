import json

from app.agent.llm import Turn, parse_turn
from app.agent.orchestrator import _parse_clarification
from app.agent.results import planning_response_format
from tests.fakes import ITINERARY_JSON


def test_output_schema_closes_every_object_and_requires_all_fields():
    response_format = planning_response_format()
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    schema = response_format["json_schema"]["schema"]
    assert schema["type"] == "object"
    assert "anyOf" not in schema

    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(schema)
    activity = schema["$defs"]["Activity"]
    assert "title" in activity["properties"]
    assert {"type": "null"} in activity["properties"]["location"]["anyOf"]
    assert "total_estimated_cost" not in schema["$defs"]["Itinerary"]["properties"]


def test_itinerary_envelope_preserves_the_existing_plan_model():
    turn = Turn(content=json.dumps({"result": json.loads(ITINERARY_JSON)}))
    itinerary, errors = parse_turn(turn)
    assert errors is None
    assert itinerary is not None
    assert itinerary.destination == json.loads(ITINERARY_JSON)["destination"]
    assert _parse_clarification(turn) is None


def test_clarification_envelope_stops_instead_of_producing_an_itinerary():
    turn = Turn(
        content=json.dumps(
            {
                "result": {
                    "clarification": {"questions": ["How many travellers?"], "reason": "inputs"}
                }
            }
        )
    )
    clarification = _parse_clarification(turn)
    assert clarification is not None
    assert clarification.questions == ["How many travellers?"]
    assert parse_turn(turn)[0] is None
