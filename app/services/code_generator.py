"""Short-code generation for media deep links.

``generate_code`` is a pure, dependency-free function (unit-testable without a
DB). ``generate_unique_code`` layers a DB uniqueness check on top of it.
"""
from __future__ import annotations

import secrets
import string

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.media import Media

# URL-safe, unambiguous alphanumeric alphabet (62 chars).
ALPHABET = string.ascii_letters + string.digits
DEFAULT_LENGTH = 8
MAX_ATTEMPTS = 10


def generate_code(length: int = DEFAULT_LENGTH) -> str:
    """Return a cryptographically-random alphanumeric code."""
    if length <= 0:
        raise ValueError("length must be positive")
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


async def generate_unique_code(
    session: AsyncSession,
    length: int = DEFAULT_LENGTH,
    max_attempts: int = MAX_ATTEMPTS,
) -> str:
    """Generate a code that does not yet exist in the ``media`` table.

    Falls back to a longer code if every attempt collides (astronomically
    unlikely, but keeps the function total).
    """
    for _ in range(max_attempts):
        code = generate_code(length)
        exists = await session.scalar(select(Media.id).where(Media.code == code))
        if exists is None:
            return code
    return generate_code(length + 4)
