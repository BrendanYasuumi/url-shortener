"""Framework-independent utility functions for short-code generation."""

BASE62_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
BASE62_BASE = len(BASE62_ALPHABET)
SHORT_CODE_LENGTH = 6
MAX_BASE62_VALUE = BASE62_BASE**SHORT_CODE_LENGTH - 1

# Decoding uses a dictionary so each character lookup is constant-time.
BASE62_VALUES = {
    character: value for value, character in enumerate(BASE62_ALPHABET)
}


def encode_base62(number: int) -> str:
    """Encode a non-negative integer as an exactly six-character Base62 code.

    Leading zeroes make every returned code the same length. The largest
    supported integer is ``62**6 - 1`` because larger values need a seventh
    Base62 character.

    Args:
        number: Integer to encode.

    Returns:
        A six-character code containing only ``0-9``, ``a-z``, and ``A-Z``.

    Raises:
        TypeError: If ``number`` is not an integer.
        ValueError: If ``number`` is negative or exceeds six-character capacity.
    """
    # Python treats bool as a subclass of int, but accepting True or False here
    # would hide a caller mistake.
    if not isinstance(number, int) or isinstance(number, bool):
        raise TypeError("number must be an integer")
    if number < 0:
        raise ValueError("number must be non-negative")
    if number > MAX_BASE62_VALUE:
        raise ValueError(
            f"number must not exceed six-character capacity ({MAX_BASE62_VALUE})"
        )

    characters = [BASE62_ALPHABET[0]] * SHORT_CODE_LENGTH
    index = SHORT_CODE_LENGTH - 1

    # Base conversion repeatedly divides by 62. Each remainder selects the next
    # character from right to left; untouched positions remain leading zeroes.
    while number > 0:
        number, remainder = divmod(number, BASE62_BASE)
        characters[index] = BASE62_ALPHABET[remainder]
        index -= 1

    return "".join(characters)


def decode_base62(short_code: str) -> int:
    """Decode an exactly six-character Base62 code into its integer value.

    Args:
        short_code: Six-character value produced by :func:`encode_base62`.

    Returns:
        The original non-negative integer.

    Raises:
        TypeError: If ``short_code`` is not a string.
        ValueError: If its length or alphabet is invalid.
    """
    if not isinstance(short_code, str):
        raise TypeError("short_code must be a string")
    if len(short_code) != SHORT_CODE_LENGTH:
        raise ValueError(
            f"short_code must contain exactly {SHORT_CODE_LENGTH} characters"
        )

    number = 0
    for character in short_code:
        try:
            value = BASE62_VALUES[character]
        except KeyError as exc:
            raise ValueError(f"invalid Base62 character: {character!r}") from exc
        number = number * BASE62_BASE + value

    return number
