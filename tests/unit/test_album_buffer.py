"""B4 unit tests — AlbumBuffer ordering, isolation, and debounce."""
from __future__ import annotations

import fakeredis.aioredis as fakeredis

from app.services.album_buffer import AlbumBuffer


def _part(fid, caption=None):
    return {"file": {"telegram_file_id": fid, "file_type": "photo"}, "caption": caption}


async def test_orders_and_pops_once():
    redis = fakeredis.FakeRedis(decode_responses=True)
    buf = AlbumBuffer(redis)
    gk = AlbumBuffer.group_key(1, 10, "G1")
    await buf.add(gk, tenant_id=1, chat_id=10, telegram_id=99, part=_part("A", "cap"), now=0)
    await buf.add(gk, tenant_id=1, chat_id=10, telegram_id=99, part=_part("B"), now=0)
    await buf.add(gk, tenant_id=1, chat_id=10, telegram_id=99, part=_part("C"), now=0)

    due = await buf.pop_due(9_999_999_999)
    assert len(due) == 1
    g = due[0]
    assert g["chat_id"] == 10 and g["telegram_id"] == 99
    assert [p["file"]["telegram_file_id"] for p in g["parts"]] == ["A", "B", "C"]
    assert g["parts"][0]["caption"] == "cap"  # first item's caption
    # claimed -> not returned again
    assert await buf.pop_due(9_999_999_999) == []


async def test_two_groups_do_not_mix():
    redis = fakeredis.FakeRedis(decode_responses=True)
    buf = AlbumBuffer(redis)
    g1 = AlbumBuffer.group_key(1, 10, "G1")
    g2 = AlbumBuffer.group_key(1, 10, "G2")
    await buf.add(g1, tenant_id=1, chat_id=10, telegram_id=1, part=_part("A"), now=0)
    await buf.add(g2, tenant_id=1, chat_id=10, telegram_id=2, part=_part("X"), now=0)
    await buf.add(g1, tenant_id=1, chat_id=10, telegram_id=1, part=_part("B"), now=0)
    await buf.add(g2, tenant_id=1, chat_id=10, telegram_id=2, part=_part("Y"), now=0)

    due = {g["group_key"]: g for g in await buf.pop_due(9_999_999_999)}
    assert [p["file"]["telegram_file_id"] for p in due[g1]["parts"]] == ["A", "B"]
    assert [p["file"]["telegram_file_id"] for p in due[g2]["parts"]] == ["X", "Y"]


async def test_not_due_not_popped():
    redis = fakeredis.FakeRedis(decode_responses=True)
    buf = AlbumBuffer(redis)
    gk = AlbumBuffer.group_key(1, 10, "G1")
    # finalize-at = 1000 + debounce; a poll "now" before that returns nothing
    await buf.add(gk, tenant_id=1, chat_id=10, telegram_id=1, part=_part("A"), now=1000)
    assert await buf.pop_due(500) == []
    assert len(await buf.pop_due(2000)) == 1
