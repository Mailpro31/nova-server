"""The Lab gateway: the only door a Lab server opens onto a LAN.

## Why this exists at all

`nova-server` listens on plain HTTP because in production something else
terminates TLS in front of it. A Lab has no such thing, and putting the API
straight onto a LAN in clear text would mean every dictation and every
transcript crossing the network readable. So the Lab publishes this instead,
and the API itself stays on the internal Docker network, unreachable from the
LAN.

## What it does, and deliberately does not do

It terminates TLS, redeems enrollment codes, and proxies enrolled devices to
the API. It does **not** authenticate users: that remains the API's own job.
Enrolling a device and signing in a person are different questions, and
answering the first here does not answer the second.

It is built from the `nova-server` image and adds no new artifact to the supply
chain: FastAPI, uvicorn, httpx and `cryptography` are already installed there,
so the Lab introduces no image, no digest and no download of its own.

## The certificate

Generated here on first start, with the Lab's LAN address in its SAN, and kept
so the fingerprint is stable across restarts. It is self-signed on purpose: the
enrollment code carries the fingerprint, so the Desktop pins this exact
certificate. That is stronger than a public CA for this shape of problem — no
issuer to trust and nothing to install on the client — and it costs the
operator nothing.

## Lab state is Lab state

Everything written here lives under the Lab directory it is given, never
alongside production configuration. The Lab has its own certificate, its own
invitations and its own device records.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import os
import secrets
import sys
import threading
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nova_lab_codes as codes  # noqa: E402

LAB_DIR = Path(os.getenv("NOVA_LAB_DIR", "/lab"))
UPSTREAM = os.getenv("NOVA_LAB_UPSTREAM", "http://api:8000")
LAB_HOST = os.getenv("NOVA_LAB_HOST", "")
LAB_PORT = int(os.getenv("NOVA_LAB_PORT", "8443"))
ORGANIZATION_ID = os.getenv("NOVA_LAB_ORGANIZATION_ID", "nova-lab")
ORGANIZATION_NAME = os.getenv("NOVA_LAB_ORGANIZATION_NAME", "Nova Lab")

CERT_FILE = LAB_DIR / "lab-cert.pem"
KEY_FILE = LAB_DIR / "lab-key.pem"
STATE_FILE = LAB_DIR / "lab-state.json"

# A device token is a bearer credential on a LAN. Short enough to be rotated by
# re-enrolling, long enough that nothing on the LAN can search it.
DEVICE_TOKEN_BYTES = 32

# Headers that belong to this hop and must not be forwarded, plus the Lab's own
# device header, which the API has no business seeing.
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
    "x-nova-lab-device",
}

_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────── Lab state

def _read_state() -> dict:
    if not STATE_FILE.exists():
        return {"schema_version": 1, "invitations": [], "devices": []}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Refuse to guess. An unreadable Lab state means no enrollment, not an
        # empty one that would silently re-open the door.
        raise RuntimeError("LAB_STATE_UNREADABLE")


def _write_state(state: dict) -> None:
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(STATE_FILE)
    try:
        os.chmod(STATE_FILE, 0o600)
    except OSError:
        pass


# ─────────────────────────────────────────────────────────── The certificate

def ensure_certificate() -> bytes:
    """Return the certificate's DER bytes, generating it once if needed."""
    LAB_DIR.mkdir(parents=True, exist_ok=True)
    if CERT_FILE.exists() and KEY_FILE.exists():
        certificate = x509.load_pem_x509_certificate(CERT_FILE.read_bytes())
        return certificate.public_bytes(serialization.Encoding.DER)

    if not LAB_HOST:
        raise RuntimeError("LAB_HOST_REQUIRED")

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Nova Lab"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORGANIZATION_NAME[:64]),
    ])
    alternatives = [x509.IPAddress(ipaddress.IPv4Address(LAB_HOST))]
    now = dt.datetime.now(dt.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        # A lab is a temporary thing. A year is long enough not to be a chore
        # and short enough that a forgotten lab stops working by itself.
        .not_valid_after(now + dt.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(alternatives), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    KEY_FILE.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError:
        pass
    CERT_FILE.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return certificate.public_bytes(serialization.Encoding.DER)


# ───────────────────────────────────────────────────────────────── The app

app = FastAPI(title="Nova Lab Gateway", docs_url=None, redoc_url=None, openapi_url=None)
_client = httpx.AsyncClient(base_url=UPSTREAM, timeout=httpx.Timeout(600.0, connect=10.0))


class EnrollRequest(BaseModel):
    code: str
    device_name: str = "nova-desktop"


@app.get("/lab/health")
def lab_health():
    """Liveness only. Says nothing about invitations, devices or the network."""
    return {"lab": True, "contract_version": codes.CODE_VERSION}


@app.get("/lab/certificate")
def lab_certificate():
    """Publish the Lab certificate without publishing an invitation.

    A Desktop first obtains these bytes over an untrusted connection, checks
    their SHA-256 against the pin carried by its invitation, then creates a
    TLS client which trusts *only* this certificate.  This endpoint therefore
    contains no secret: an on-path attacker can replace its response, but can
    never make the replacement match the invitation's 128-bit pin.
    """
    return Response(
        content=ensure_certificate(),
        media_type="application/pkix-cert",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/lab/enroll")
def lab_enroll(body: EnrollRequest):
    """Redeem an invitation once, and return what the Desktop needs to join."""
    try:
        invitation = codes.decode(body.code)
    except codes.LabCodeError:
        # One message for every bad code: a caller learns whether a code works,
        # never how close it came.
        raise HTTPException(403, "LAB_CODE_INVALID")

    now = dt.datetime.now(dt.timezone.utc).timestamp()
    with _lock:
        state = _read_state()
        matched = None
        for record in state["invitations"]:
            candidate = codes.hash_secret(invitation.secret, bytes.fromhex(record["salt"]))
            if codes.constant_time_equals(candidate, record["secret_hash"]):
                matched = record
                break
        if matched is None:
            raise HTTPException(403, "LAB_CODE_INVALID")
        if matched["expires_at"] < now:
            raise HTTPException(403, "LAB_CODE_INVALID")
        if matched["remaining_uses"] <= 0:
            raise HTTPException(403, "LAB_CODE_INVALID")

        matched["remaining_uses"] -= 1
        token = secrets.token_urlsafe(DEVICE_TOKEN_BYTES)
        state["devices"].append({
            "device_name": body.device_name[:64],
            "token_hash": codes.hash_secret(token.encode(), b"lab-device"),
            "enrolled_at": now,
        })
        _write_state(state)

    return {
        "contract_version": codes.CODE_VERSION,
        "organization": {"slug": ORGANIZATION_ID, "display_name": ORGANIZATION_NAME},
        "service_endpoint": f"https://{LAB_HOST}:{LAB_PORT}",
        "deployment_mode": "lab",
        "device_token": token,
    }


def _device_is_enrolled(token: str | None) -> bool:
    if not token:
        return False
    digest = codes.hash_secret(token.encode(), b"lab-device")
    try:
        state = _read_state()
    except RuntimeError:
        return False
    return any(
        codes.constant_time_equals(digest, device["token_hash"])
        for device in state["devices"]
    )


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy(path: str, request: Request,
                x_nova_lab_device: str | None = Header(default=None)):
    """Forward an enrolled device to the API, and nobody else.

    This is a second lock in front of the API's own authentication, not a
    replacement for it: the device proves it was invited, the user still signs
    in. Being on the LAN is not a credential.
    """
    if not _device_is_enrolled(x_nova_lab_device):
        raise HTTPException(401, "LAB_DEVICE_NOT_ENROLLED")

    headers = {
        key: value for key, value in request.headers.items()
        if key.lower() not in _HOP_BY_HOP
    }
    upstream = await _client.request(
        request.method,
        "/" + path,
        content=await request.body(),
        headers=headers,
        params=dict(request.query_params),
    )
    passthrough = {
        key: value for key, value in upstream.headers.items()
        if key.lower() not in _HOP_BY_HOP
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=passthrough,
        media_type=upstream.headers.get("content-type"),
    )


def main() -> int:
    import uvicorn

    der = ensure_certificate()
    fingerprint = codes.fingerprint_of(der)
    # The fingerprint is public: it is what the Desktop pins. Printing it is how
    # the operator can confirm a code refers to this server and no other.
    print("LAB_CERT_SHA256 " + fingerprint.hex(), flush=True)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=LAB_PORT,
        ssl_certfile=str(CERT_FILE),
        ssl_keyfile=str(KEY_FILE),
        log_level=os.getenv("NOVA_LAB_LOG_LEVEL", "warning"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
