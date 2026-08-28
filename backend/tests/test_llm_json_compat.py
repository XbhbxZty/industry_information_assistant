import os
import sys
from typing import Any, Dict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from service.deep_research_v2.agents.base import BaseAgent  # noqa: E402


class _Agent(BaseAgent):
    async def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return state


def _agent() -> _Agent:
    return _Agent(
        name="json-test",
        role="test",
        llm_api_key="test-key",
        llm_base_url="http://127.0.0.1:1/v1",
        model="test-model",
    )


def test_parse_json_unwraps_singleton_object_array():
    parsed = _agent().parse_json_response('[{"issues": [], "ok": true}]')

    assert parsed == {"issues": [], "ok": True}


def test_parse_json_unwraps_singleton_object_array_in_code_fence():
    parsed = _agent().parse_json_response(
        '```json\n[{"extracted_facts": [], "field_evidence": []}]\n```'
    )

    assert parsed == {"extracted_facts": [], "field_evidence": []}


def test_parse_json_rejects_ambiguous_top_level_array_without_leaking_list():
    parsed = _agent().parse_json_response('[{"id": 1}, {"id": 2}]')

    assert parsed == {}
    assert isinstance(parsed, dict)
