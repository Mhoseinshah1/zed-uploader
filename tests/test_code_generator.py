"""Unit tests for the code generator (no DB required)."""
from __future__ import annotations

import pytest

from app.services.code_generator import ALPHABET, DEFAULT_LENGTH, generate_code


def test_generate_code_length_and_alphabet():
    code = generate_code()
    assert len(code) == DEFAULT_LENGTH
    assert all(char in ALPHABET for char in code)


def test_generate_code_custom_length():
    code = generate_code(16)
    assert len(code) == 16


def test_generate_code_rejects_non_positive_length():
    with pytest.raises(ValueError):
        generate_code(0)


def test_generate_code_uniqueness():
    # 5000 8-char base-62 codes should be unique with overwhelming probability.
    codes = {generate_code() for _ in range(5000)}
    assert len(codes) == 5000
