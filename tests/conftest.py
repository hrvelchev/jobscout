"""Shared test harness.

The Anthropic client is replaced with a scripted fake (pattern inherited from
the author's marketing-bot-template test suite): each call pops the next
scripted response and records the request payload in `calls`, so tests assert
on exactly what was sent. When one response remains it repeats forever - that
is what makes retry-bound tests terminate.

The network is disabled at socket level by pytest-socket (see pyproject
addopts); only localhost is allowed, for the pg-marked integration tests.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))  # so `from fakes import ...` works

from fakes import FakeEmbedder, FakeStore  # noqa: E402


class FakeTextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class FakeResponse:
    def __init__(self, text: str, input_tokens: int = 100, output_tokens: int = 50):
        self.content = [FakeTextBlock(text)]
        self.stop_reason = "end_turn"
        self.usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)


class FakeMessages:
    def __init__(self, responses: list[FakeResponse | Exception]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs) -> FakeResponse:
        self.calls.append(kwargs)
        item = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        if isinstance(item, Exception):
            if self._responses and self._responses[0] is item:
                self._responses.pop(0)  # an exception never repeats
            raise item
        return item


class FakeAnthropicClient:
    def __init__(self, responses: list[FakeResponse | Exception]):
        self.messages = FakeMessages(responses)

    @property
    def calls(self) -> list[dict]:
        return self.messages.calls


def text_response(text: str, **kwargs) -> FakeResponse:
    return FakeResponse(text, **kwargs)


def json_response(payload_json: str, **kwargs) -> FakeResponse:
    return FakeResponse(payload_json, **kwargs)


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def config_dir() -> Path:
    """The repo's committed example config - tests prove the examples work."""
    return Path(__file__).parent.parent / "config"
