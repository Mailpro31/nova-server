"""Enrollment codes for the Lab: what a Desktop needs to join, and nothing else.

A Lab server lives on a LAN, at an address that is meaningless outside it and
that nobody should be asked to type. The enrollment code carries that address
*and* the identity of the certificate to expect, so joining is one paste rather
than an IP, a port and a leap of faith.

The code is the whole trust decision, so it says exactly four things:

    version | address | port | certificate fingerprint | one-time secret

The fingerprint is what makes this safe without a certificate authority. The
Desktop pins the certificate named by the code and refuses anything else, so a
machine that answers on the same address with a different certificate is not
mistaken for the Lab. Truncating the fingerprint to 128 bits is deliberate: a
pin needs second-preimage resistance, not collision resistance, and 128 bits of
SHA-256 is far past what an attacker on a LAN can search.

The secret is the invitation. It is never stored anywhere in this form: the
server keeps only a salted hash, a deadline and a remaining-use count.

The code is not a credential for a person. Redeeming it enrolls a device; the
user still authenticates afterwards.
"""

from __future__ import annotations

import hashlib
import ipaddress
import secrets
from dataclasses import dataclass

# Bumped only if the byte layout changes. A Desktop that meets a version it does
# not know refuses the code rather than reading the wrong bytes as an address.
CODE_VERSION = 1

PREFIX = "NOVA-LAB1"
GROUP = 5

FINGERPRINT_BYTES = 16  # 128 bits of SHA-256, enough for a pin
SECRET_BYTES = 16  # 128 bits of invitation
PAYLOAD_BYTES = 1 + 4 + 2 + FINGERPRINT_BYTES + SECRET_BYTES

# Crockford base32: 32 symbols with I, L, O and U left out, so a code read off
# one screen and typed into another machine does not turn into a different code.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
# The characters people substitute anyway, mapped back on the way in.
_CONFUSABLE = {"I": "1", "L": "1", "O": "0"}


class LabCodeError(ValueError):
    """The code is not one this build can read. Never says which part failed."""


@dataclass(frozen=True)
class LabInvitation:
    """What a code means once decoded. `secret` is present only in transit."""

    host: str
    port: int
    fingerprint: bytes
    secret: bytes

    @property
    def service_endpoint(self) -> str:
        return f"https://{self.host}:{self.port}"


def _b32_encode(raw: bytes) -> str:
    value = int.from_bytes(raw, "big")
    digits = []
    for _ in range((len(raw) * 8 + 4) // 5):
        digits.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(digits))


def _b32_decode(text: str) -> bytes:
    value = 0
    for character in text:
        character = _CONFUSABLE.get(character, character)
        index = _ALPHABET.find(character)
        if index < 0:
            raise LabCodeError("invalid code")
        value = (value << 5) | index
    try:
        # 63 symbols carry 315 bits for a 312-bit payload. The three spare top
        # bits must be zero; a code that sets them is malformed, not a bigger
        # number to be truncated into something that looks valid.
        return value.to_bytes(PAYLOAD_BYTES, "big")
    except OverflowError as error:
        raise LabCodeError("invalid code") from error


def encode(host: str, port: int, fingerprint: bytes, secret: bytes) -> str:
    """Render an invitation as the string the operator reads out."""
    try:
        address = ipaddress.IPv4Address(host)
    except ipaddress.AddressValueError as error:
        # IPv6 and hostnames are deliberately out of scope: a Lab is one LAN,
        # and a wider address space here would be surface without a purpose.
        raise LabCodeError("only IPv4 lab addresses are supported") from error
    if not 1 <= port <= 65535:
        raise LabCodeError("port out of range")
    if len(fingerprint) < FINGERPRINT_BYTES or len(secret) != SECRET_BYTES:
        raise LabCodeError("bad invitation material")

    payload = (
        bytes([CODE_VERSION])
        + address.packed
        + port.to_bytes(2, "big")
        + fingerprint[:FINGERPRINT_BYTES]
        + secret
    )
    body = _b32_encode(payload)
    groups = [body[i : i + GROUP] for i in range(0, len(body), GROUP)]
    return PREFIX + "-" + "-".join(groups)


def decode(code: str) -> LabInvitation:
    """Read a code back, or refuse it.

    Separators are advisory. This code gets read off one screen and typed into
    another machine, so spaces, dashes and case are normalised away rather than
    turned into a failure the person cannot see.
    """
    # A code travels between two machines, so it arrives through clipboards,
    # text files and editors. Byte-order marks and zero-width characters ride
    # along invisibly; refusing them would look like refusing a correct code.
    invisible = "﻿​‌‍⁠ "
    stripped = "".join(character for character in code if character not in invisible)
    cleaned = "".join(stripped.split()).replace("-", "").replace("_", "").upper()
    bare_prefix = PREFIX.replace("-", "")
    if not cleaned.startswith(bare_prefix):
        raise LabCodeError("invalid code")
    body = cleaned[len(bare_prefix) :]
    expected = (PAYLOAD_BYTES * 8 + 4) // 5
    if len(body) != expected:
        raise LabCodeError("invalid code")

    payload = _b32_decode(body)
    if payload[0] != CODE_VERSION:
        raise LabCodeError("unsupported code version")
    host = str(ipaddress.IPv4Address(payload[1:5]))
    port = int.from_bytes(payload[5:7], "big")
    fingerprint = payload[7 : 7 + FINGERPRINT_BYTES]
    secret = payload[7 + FINGERPRINT_BYTES :]
    return LabInvitation(host=host, port=port, fingerprint=fingerprint, secret=secret)


def new_secret() -> bytes:
    return secrets.token_bytes(SECRET_BYTES)


def hash_secret(secret: bytes, salt: bytes) -> str:
    """Salted digest of an invitation secret.

    The stored form must not let someone who reads the Lab state file replay an
    invitation, and the salt keeps two identical secrets from looking identical.
    """
    return hashlib.sha256(salt + secret).hexdigest()


def fingerprint_of(certificate_der: bytes) -> bytes:
    """The full SHA-256 of a certificate. Truncated only when put into a code."""
    return hashlib.sha256(certificate_der).digest()


def constant_time_equals(left: str, right: str) -> bool:
    return secrets.compare_digest(left, right)
