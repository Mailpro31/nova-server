"""Issue a Lab invitation. Run by the operator, on the Lab machine.

The secret is generated here, shown once, and never stored: the Lab keeps only
a salted hash, a deadline and a remaining-use count. Losing the code means
issuing another one, which is the correct trade for an invitation that cannot
be recovered from the server it opens.

    python nova_lab_invite.py --hours 2 --uses 1
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import secrets
import sys
from pathlib import Path

from cryptography import x509

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nova_lab_codes as codes  # noqa: E402

LAB_DIR = Path(os.getenv("NOVA_LAB_DIR", "/lab"))
STATE_FILE = LAB_DIR / "lab-state.json"
CERT_FILE = LAB_DIR / "lab-cert.pem"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("NOVA_LAB_HOST", ""))
    parser.add_argument("--port", type=int, default=int(os.getenv("NOVA_LAB_PORT", "8443")))
    parser.add_argument("--hours", type=float, default=2.0, help="lifetime of the invitation")
    parser.add_argument("--uses", type=int, default=1, help="how many devices it may enroll")
    arguments = parser.parse_args()

    if not arguments.host:
        print("LAB_HOST_REQUIRED", file=sys.stderr)
        return 2
    if not CERT_FILE.exists():
        # Without the certificate there is nothing to pin, and an invitation
        # that cannot pin is an invitation to talk to anyone.
        print("LAB_CERTIFICATE_MISSING", file=sys.stderr)
        return 2
    if arguments.uses < 1 or arguments.hours <= 0:
        print("LAB_INVITATION_BOUNDS_INVALID", file=sys.stderr)
        return 2

    certificate = x509.load_pem_x509_certificate(CERT_FILE.read_bytes())
    from cryptography.hazmat.primitives import serialization

    fingerprint = codes.fingerprint_of(
        certificate.public_bytes(serialization.Encoding.DER)
    )

    secret = codes.new_secret()
    salt = secrets.token_bytes(16)
    expires_at = (
        dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=arguments.hours)
    ).timestamp()

    state = {"schema_version": 1, "invitations": [], "devices": []}
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    # Spent and expired invitations are dropped rather than accumulated: the
    # file should not grow into a list of things that can never be used.
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    state["invitations"] = [
        record for record in state["invitations"]
        if record["remaining_uses"] > 0 and record["expires_at"] > now
    ]
    state["invitations"].append({
        "salt": salt.hex(),
        "secret_hash": codes.hash_secret(secret, salt),
        "expires_at": expires_at,
        "remaining_uses": arguments.uses,
        "issued_at": now,
    })
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(STATE_FILE)
    try:
        os.chmod(STATE_FILE, 0o600)
    except OSError:
        pass

    code = codes.encode(arguments.host, arguments.port, fingerprint, secret)
    print(code)
    print(
        "LAB_INVITATION_ISSUED uses={0} expires_in_hours={1}".format(
            arguments.uses, arguments.hours
        ),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
