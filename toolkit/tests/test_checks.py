import json

from frontieror.checks import check_solution

SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            "objective_value": {"type": "number"},
            "variant": {"const": "quadratic"},
            "pair": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
        },
        "required": ["objective_value", "variant"],
    }
)


def test_json_schema_checks():
    ok = check_solution({"objective_value": 3, "variant": "quadratic", "pair": [1, 2]}, SCHEMA)
    assert ok.status == "ok" and ok.objective == 3

    bad = check_solution({"objective_value": 3, "variant": "linear", "pair": [1, "x"]}, SCHEMA)
    assert bad.status == "invalid_format"
    assert "variant:" in bad.message and "pair[1]:" in bad.message


def test_objective_value_is_always_checked():
    assert check_solution({"objective_value": True}, "{}").status == "invalid_format"
    assert check_solution({"objective_value": 1.5}, "{}").status == "ok"
