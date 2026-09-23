import pytest
from pydantic import ValidationError

from app.schemas import MessageReactionRequest


def test_named_reactions_and_legacy_request_key():
    assert MessageReactionRequest.model_validate({"reaction": "heart"}).reaction == "heart"
    assert MessageReactionRequest.model_validate({"emoji": "paw"}).reaction == "paw"


def test_unsupported_reaction_is_rejected():
    with pytest.raises(ValidationError):
        MessageReactionRequest.model_validate({"reaction": "\U0001f431"})
