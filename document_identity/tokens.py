"""Canonical opaque keys. Printed identity never confers authorization."""
import re
import secrets

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
TOKEN = re.compile(r"T1-[0-7][0-9A-HJKMNP-TV-Z]{25}\Z", re.ASCII)
ALIAS = re.compile(r"A1-[0-9A-HJKMNP-TV-Z]{10}\Z", re.ASCII)


def encode_token(raw):
    if not isinstance(raw, bytes) or len(raw) != 16:
        raise ValueError("A teaching token requires exactly 16 bytes.")
    number = int.from_bytes(raw, "big")
    digits = []
    for _ in range(26):
        digits.append(ALPHABET[number & 31])
        number >>= 5
    return "T1-" + "".join(reversed(digits))


def parse_token(value):
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise ValueError("Invalid or unsupported teaching token.")
    number = 0
    for char in value[3:]:
        number = number * 32 + ALPHABET.index(char)
    return number.to_bytes(16, "big")


def generate_token():
    return encode_token(secrets.token_bytes(16))


def generate_alias():
    return "A1-" + "".join(secrets.choice(ALPHABET) for _ in range(10))


def valid_alias(value):
    return isinstance(value, str) and ALIAS.fullmatch(value) is not None
