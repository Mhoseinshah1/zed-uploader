"""F3 unit test — BotFather token validation via getMe (mock Bot, no network)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.bot_creation_service as svc


class _FakeBot:
    def __init__(self, token, get_me=None):
        self.token = token
        self._get_me = get_me
        self.session = SimpleNamespace(close=AsyncMock())

    async def get_me(self):
        if isinstance(self._get_me, Exception):
            raise self._get_me
        return self._get_me


async def test_validate_token_returns_bot_id_and_username(monkeypatch):
    me = SimpleNamespace(id=555001, username="cust_bot")
    monkeypatch.setattr(svc, "Bot", lambda token: _FakeBot(token, get_me=me))
    bot_id, username = await svc.validate_bot_token("123:GOOD")
    assert bot_id == 555001 and username == "cust_bot"


async def test_validate_token_raises_on_invalid(monkeypatch):
    monkeypatch.setattr(
        svc, "Bot", lambda token: _FakeBot(token, get_me=RuntimeError("401"))
    )
    with pytest.raises(RuntimeError):
        await svc.validate_bot_token("123:BAD")
