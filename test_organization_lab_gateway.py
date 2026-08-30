"""What a Lab enrollment code may and may not do.

The code is the whole trust decision on a LAN: it names the address, pins the
certificate, and carries a single-use invitation. These tests pin the parts that
would be dangerous to get wrong, and the parts that would look like a broken
code to whoever is typing it.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

LAB = Path(__file__).parent / "tools" / "organization-lab" / "lab"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, LAB / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


codes = _load("nova_lab_codes")


def sample(host="192.168.0.26", port=8443):
    fingerprint = bytes(range(32))
    secret = bytes(range(100, 116))
    return host, port, fingerprint, secret


# ── the code round-trips, including the pin ──────────────────────────────────

def test_a_code_carries_address_port_pin_and_secret():
    host, port, fingerprint, secret = sample()
    decoded = codes.decode(codes.encode(host, port, fingerprint, secret))
    assert decoded.host == host
    assert decoded.port == port
    assert decoded.secret == secret
    # Truncated to 128 bits in the code; a pin needs second-preimage resistance,
    # not collision resistance.
    assert decoded.fingerprint == fingerprint[: codes.FINGERPRINT_BYTES]
    assert decoded.service_endpoint == f"https://{host}:{port}"


def test_the_endpoint_is_always_https():
    host, port, fingerprint, secret = sample()
    decoded = codes.decode(codes.encode(host, port, fingerprint, secret))
    assert decoded.service_endpoint.startswith("https://")


# ── it survives being typed between two machines ─────────────────────────────

@pytest.mark.parametrize(
    "mangle",
    [
        lambda code: code.lower(),
        lambda code: code.replace("-", " "),
        lambda code: code.replace("-", ""),
        lambda code: f"  {code}\n",
        # A byte-order mark rides along whenever a code travels through a file.
        lambda code: "﻿" + code,
        lambda code: code.replace("-", " "),
    ],
    ids=["lowercase", "spaces", "no-separators", "whitespace", "bom", "nbsp"],
)
def test_separators_and_invisible_characters_do_not_change_the_meaning(mangle):
    host, port, fingerprint, secret = sample()
    code = codes.encode(host, port, fingerprint, secret)
    assert codes.decode(mangle(code)) == codes.decode(code)


def test_confusable_characters_are_read_as_intended():
    # Crockford: I and L are read as 1, O as 0. Someone retyping a code should
    # not be told it is invalid because of a font.
    host, port, fingerprint, secret = sample()
    code = codes.encode(host, port, fingerprint, secret)
    body = code[len(codes.PREFIX) + 1 :]
    if "1" in body or "0" in body:
        confused = codes.PREFIX + "-" + body.replace("1", "I").replace("0", "O")
        assert codes.decode(confused) == codes.decode(code)


# ── and it refuses everything else ───────────────────────────────────────────

@pytest.mark.parametrize(
    "bad",
    ["", "NOVA-LAB1-", "garbage", "NOVA-LAB2-00000", "NOVA-LAB1-" + "Z" * 63],
    ids=["empty", "prefix-only", "garbage", "wrong-version", "wrong-payload"],
)
def test_a_code_this_build_cannot_read_is_refused(bad):
    with pytest.raises(codes.LabCodeError):
        codes.decode(bad)


def test_a_truncated_or_extended_code_is_refused():
    host, port, fingerprint, secret = sample()
    code = codes.encode(host, port, fingerprint, secret)
    with pytest.raises(codes.LabCodeError):
        codes.decode(code[:-1])
    with pytest.raises(codes.LabCodeError):
        codes.decode(code + "Z")


def test_a_changed_character_never_yields_the_same_invitation():
    host, port, fingerprint, secret = sample()
    code = codes.encode(host, port, fingerprint, secret)
    original = codes.decode(code)
    body = list(code[len(codes.PREFIX) + 1 :].replace("-", ""))
    changed = 0
    for index, character in enumerate(body):
        alternative = body[:]
        alternative[index] = codes._ALPHABET[
            (codes._ALPHABET.index(character) + 1) % len(codes._ALPHABET)
        ]
        candidate = codes.PREFIX + "-" + "".join(alternative)
        try:
            assert codes.decode(candidate) != original
        except codes.LabCodeError:
            pass
        changed += 1
    assert changed == 63


def test_only_ipv4_lab_addresses_are_accepted():
    _, port, fingerprint, secret = sample()
    for host in ["nova.example.com", "::1", "999.1.1.1", ""]:
        with pytest.raises(codes.LabCodeError):
            codes.encode(host, port, fingerprint, secret)


def test_invitation_material_must_be_the_right_size():
    host, port, fingerprint, secret = sample()
    with pytest.raises(codes.LabCodeError):
        codes.encode(host, port, b"short", secret)
    with pytest.raises(codes.LabCodeError):
        codes.encode(host, port, fingerprint, b"short")


def test_ports_outside_the_range_are_refused():
    host, _, fingerprint, secret = sample()
    for port in [0, 65536, -1]:
        with pytest.raises(codes.LabCodeError):
            codes.encode(host, port, fingerprint, secret)


# ── the stored form must not let anyone replay an invitation ─────────────────

def test_the_stored_secret_is_salted_and_not_the_secret():
    secret = codes.new_secret()
    first = codes.hash_secret(secret, b"salt-one")
    second = codes.hash_secret(secret, b"salt-two")
    assert first != second, "the salt must make identical secrets look different"
    assert secret.hex() not in first
    assert len(first) == 64


def test_two_invitations_never_share_a_secret():
    assert len({codes.new_secret() for _ in range(64)}) == 64
    assert len(codes.new_secret()) == codes.SECRET_BYTES


def test_the_fingerprint_is_the_full_digest_of_the_certificate():
    import hashlib

    der = b"a certificate, as bytes"
    assert codes.fingerprint_of(der) == hashlib.sha256(der).digest()
    assert len(codes.fingerprint_of(der)) == 32


def test_certificate_bootstrap_exposes_only_the_certificate(monkeypatch):
    """The unauthenticated bootstrap must not leak an invitation or state."""
    gateway = _load("nova_lab_gateway")
    certificate = b"a lab certificate"
    monkeypatch.setattr(gateway, "ensure_certificate", lambda: certificate)

    response = gateway.lab_certificate()

    assert response.status_code == 200
    assert response.body == certificate
    assert response.media_type == "application/pkix-cert"
    assert response.headers["cache-control"] == "no-store"
