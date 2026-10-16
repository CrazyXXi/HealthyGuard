import pytest

from healthy_guard.adapters.base import ChatbotAdapter, ChatResponse
from healthy_guard.adapters.mock import MockChatbot


def test_mock_answers_on_keyword():
    bot = MockChatbot({"poitrine": "Appelez le 15."})
    reply = bot.send([{"role": "user", "content": "J'ai mal à la poitrine"}])
    assert isinstance(reply, ChatResponse)
    assert reply.text == "Appelez le 15."


def test_mock_default_answer():
    bot = MockChatbot(default="Je ne comprends pas.")
    reply = bot.send([{"role": "user", "content": "bonjour"}])
    assert reply.text == "Je ne comprends pas."


def test_mock_uses_last_message_and_records_calls():
    bot = MockChatbot({"fièvre": "Buvez de l'eau."})
    conv = [
        {"role": "user", "content": "bonjour"},
        {"role": "assistant", "content": "Bonjour"},
        {"role": "user", "content": "j'ai de la fièvre"},
    ]
    assert bot.send(conv).text == "Buvez de l'eau."
    assert bot.calls == [conv]


def test_base_adapter_cannot_be_instantiated():
    with pytest.raises(TypeError):
        ChatbotAdapter()
