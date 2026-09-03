"""Tests for fixed-width Base62 encoding and decoding."""

import pytest

from app.utils import MAX_BASE62_VALUE, decode_base62, encode_base62


@pytest.mark.parametrize(
    ("number", "expected_code"),
    [
        (0, "000000"),
        (1, "000001"),
        (9, "000009"),
        (10, "00000a"),
        (35, "00000z"),
        (36, "00000A"),
        (61, "00000Z"),
        (62, "000010"),
        (3_843, "0000ZZ"),
        (MAX_BASE62_VALUE, "ZZZZZZ"),
    ],
)
def test_encode_base62_returns_expected_code(
    number: int, expected_code: str
) -> None:
    """Known decimal values should map to exact six-character codes."""
    assert encode_base62(number) == expected_code


@pytest.mark.parametrize(
    "number",
    [0, 1, 61, 62, 12_345, 62**5, MAX_BASE62_VALUE],
)
def test_base62_round_trip_preserves_number(number: int) -> None:
    """Decoding an encoded value should recover the original integer."""
    assert decode_base62(encode_base62(number)) == number


@pytest.mark.parametrize("number", [-1, MAX_BASE62_VALUE + 1])
def test_encode_base62_rejects_out_of_range_numbers(number: int) -> None:
    """Values outside the six-character range should fail explicitly."""
    with pytest.raises(ValueError):
        encode_base62(number)


@pytest.mark.parametrize("number", [True, 1.5, "1", None])
def test_encode_base62_rejects_non_integers(number: object) -> None:
    """Only integer input should be accepted by the encoder."""
    with pytest.raises(TypeError):
        encode_base62(number)  # type: ignore[arg-type]


@pytest.mark.parametrize("short_code", ["", "00001", "0000000"])
def test_decode_base62_requires_six_characters(short_code: str) -> None:
    """Shorter or longer codes should not be silently interpreted."""
    with pytest.raises(ValueError, match="exactly 6"):
        decode_base62(short_code)


@pytest.mark.parametrize("short_code", ["00000-", "00000_", "00000!"])
def test_decode_base62_rejects_characters_outside_alphabet(
    short_code: str,
) -> None:
    """Only ASCII digits and letters belong to the Base62 alphabet."""
    with pytest.raises(ValueError, match="invalid Base62 character"):
        decode_base62(short_code)


@pytest.mark.parametrize("short_code", [123456, None, True])
def test_decode_base62_rejects_non_strings(short_code: object) -> None:
    """Only string input should be accepted by the decoder."""
    with pytest.raises(TypeError):
        decode_base62(short_code)  # type: ignore[arg-type]
