import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from qbrowser.action_guard import ActionGuard, combine_impact, normalize_review


def test_independent_review_escalates_uncertain_or_low_confidence_actions():
    uncertain = normalize_review(json.dumps({
        "impact": "uncertain", "confidence": 0.4, "reason": "Target unclear"}))
    assert combine_impact("local", uncertain) == "external"
    low_confidence = {"impact": "local", "confidence": 0.72, "reason": "Maybe local"}
    assert combine_impact("local", low_confidence) == "external"


def test_guard_never_downgrades_external_or_destructive_classification():
    local = {"impact": "local", "confidence": 1.0, "reason": "Local window"}
    assert combine_impact("external", local) == "external"
    destructive = {"impact": "destructive", "confidence": 0.99, "reason": "Delete control"}
    assert combine_impact("local", destructive) == "destructive"


def test_action_guard_uses_independent_model_without_sending_pasted_content():
    class FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
            self.request = None

        def create(self, **request):
            self.request = request
            message = SimpleNamespace(content=json.dumps({
                "impact": "local", "confidence": 0.97, "reason": "Local editor"}))
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    client = FakeClient()
    job = {"id": "job-1", "instruction": "Type a private note in the local editor",
           "provider": "custom", "model": "executor", "external_approved": False}
    args = {"x": 4, "y": 5, "text": "private note contents"}
    with patch("qbrowser.verification._configuration",
               return_value=("custom", "independent-reviewer", "http://localhost/v1", "key")):
        result = ActionGuard(client).review(job, "type", args, "Enter note", "data:image/jpeg;base64,AA==")
    request = client.request["messages"][1]["content"][0]["text"]
    assert result["impact"] == "local"
    assert "private note contents" not in request
    assert json.loads(request)["target_coordinates"]["text_character_count"] == len("private note contents")
    assert client.request["model"] == "independent-reviewer"


@pytest.mark.parametrize("raw", ["[]", "{", '{"impact":"local","confidence":2}'])
def test_guard_rejects_malformed_or_unbounded_reviews(raw):
    with pytest.raises(ValueError):
        normalize_review(raw)
