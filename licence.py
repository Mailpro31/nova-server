"""La licence signée de Nova (4.1).

L'éditeur crée la licence d'une école sur son PC, avec sa clé privée, qui ne
le quitte jamais ; le serveur de l'école la vérifie avec la clé publique
intégrée ci-dessous. La licence n'est pas un secret : elle est signée, et une
licence modifiée ou signée par une autre clé est refusée.

Une licence échue n'arrête jamais la dictée : un avertissement d'abord, puis
un délai de grâce, puis seulement les fonctions annexes s'arrêtent (`main`).

Sur le PC de l'éditeur :

    python licence.py keygen C:\\chemin\\nova-licence.key
    python licence.py issue C:\\chemin\\nova-licence.key --organization IPSA \\
        --tier 151-250 --kind subscription --starts 2026-09-01 --ends 2027-08-31
"""

from __future__ import annotations

import argparse
import datetime
import math
import sys
import time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# Intégrée au code, jamais lue d'une variable d'environnement : une école qui
# la remplacerait signerait ses propres licences.
# shortcut: vide tant que l'éditeur n'a pas créé sa paire de clés (`keygen`) ;
# toute licence est alors « invalid », sans rien couper.
PUBLIC_KEY_PEM = ""

WARNING_DAYS = 30
GRACE_DAYS = 30
KINDS = ("pilot", "subscription")
FIELDS = ("organization", "tier", "kind", "starts", "ends")
DAY = 86400


def day_stamp(day: str) -> float:
    """« 2027-08-31 » → minuit UTC de ce jour."""
    return datetime.datetime.strptime(day, "%Y-%m-%d").replace(
        tzinfo=datetime.timezone.utc).timestamp()


def new_key_pair() -> tuple[str, str]:
    """(clé privée, clé publique), en PEM."""
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return private_pem, public_pem


def issue(private_pem: str, *, organization: str, tier: str, kind: str,
          starts: str, ends: str, ai_envelope_eur: float | None = None) -> str:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if day_stamp(ends) < day_stamp(starts):
        raise ValueError("ends before starts")
    claims = {"organization": organization, "tier": tier, "kind": kind,
              "starts": starts, "ends": ends}
    if ai_envelope_eur is not None:
        if ai_envelope_eur < 0:
            raise ValueError("ai_envelope_eur must be positive")
        claims["ai_envelope_eur"] = ai_envelope_eur
    return jwt.encode(claims, private_pem, algorithm="EdDSA")


def licence_state(token: str, now: float | None = None,
                  public_pem: str | None = None) -> dict:
    """`none`, `invalid`, `valid`, `warning`, `grace` ou `expired`.

    `degraded` n'est vrai que pour une licence authentique échue depuis plus
    que le délai de grâce : ni l'absence de licence ni une faute de copie ne
    coupent quoi que ce soit.
    """
    token = (token or "").strip()
    if not token:
        return {"status": "none", "degraded": False}
    try:
        claims = jwt.decode(token, public_pem or PUBLIC_KEY_PEM, algorithms=["EdDSA"])
        fields = {name: str(claims[name]) for name in FIELDS}
        end = day_stamp(fields["ends"]) + DAY  # la licence couvre son dernier jour
        # L'enveloppe d'IA (2.3), en euros ; absente, aucune enveloppe ne s'applique.
        envelope = claims.get("ai_envelope_eur")
        fields["ai_envelope_eur"] = float(envelope) if envelope is not None else None
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        return {"status": "invalid", "degraded": False}
    left = end - (time.time() if now is None else now)
    if left > WARNING_DAYS * DAY:
        status = "valid"
    elif left > 0:
        status = "warning"
    elif left > -GRACE_DAYS * DAY:
        status = "grace"
    else:
        status = "expired"
    return {"status": status, "degraded": status == "expired",
            "days_left": math.ceil(left / DAY), "grace_days": GRACE_DAYS, **fields}


def _cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Licences Nova (PC de l'éditeur).")
    commands = parser.add_subparsers(dest="command", required=True)
    keygen = commands.add_parser("keygen", help="crée la paire de clés")
    keygen.add_argument("private_key_file", type=Path)
    make = commands.add_parser("issue", help="signe la licence d'une école")
    make.add_argument("private_key_file", type=Path)
    for name in ("organization", "tier", "starts", "ends"):
        make.add_argument(f"--{name}", required=True)
    make.add_argument("--kind", required=True, choices=KINDS)
    make.add_argument("--ai-envelope-eur", type=float, help="enveloppe d'IA du contrat, en euros")
    args = parser.parse_args(argv)

    if args.command == "keygen":
        if args.private_key_file.exists():
            print("Ce fichier existe déjà : une clé n'est jamais écrasée.", file=sys.stderr)
            return 1
        private_pem, public_pem = new_key_pair()
        args.private_key_file.write_text(private_pem, encoding="utf-8")
        print("Clé privée écrite. Elle ne quitte jamais ce PC.\n"
              "Clé publique, à mettre dans PUBLIC_KEY_PEM (licence.py) :\n")
        print(public_pem)
        return 0
    print(issue(args.private_key_file.read_text(encoding="utf-8"),
                organization=args.organization, tier=args.tier, kind=args.kind,
                starts=args.starts, ends=args.ends, ai_envelope_eur=args.ai_envelope_eur))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
