"""Outil d'opérateur Nova — attribution des rôles de sécurité.

## Pourquoi une commande locale, et pas un endpoint

Le premier administrateur d'une organisation ne peut pas naître d'un appel HTTP.
Un endpoint « faites de moi un administrateur » est une porte dérobée, quelles
que soient les conditions qu'on lui ajoute : il suffit qu'une seule d'entre
elles se relâche un jour. La seule autorité acceptable ici est celle de qui
détient déjà le serveur — donc une commande locale, exécutée sur la machine, à
côté de la base.

Il n'y a pas non plus de variable d'environnement d'amorçage : elle resterait en
place bien après le premier démarrage, et une variable oubliée est une porte
dérobée permanente.

Usage :

    python admin_cli.py grant <email> <role>
    python admin_cli.py revoke <email>
    python admin_cli.py list

Rôles : organization_admin, it_admin, read_only, member.
"""

import sys

import main


def _print_usage():
    print(__doc__.strip())


def grant(email: str, role: str) -> int:
    try:
        result = main.set_security_role(email, role)
    except (ValueError, LookupError) as error:
        print(f"refusé : {error}")
        return 1
    print(f"{result['email']} → {result['security_role']}")
    return 0


def revoke(email: str) -> int:
    """Retire tout privilège d'administration, sans toucher au compte.

    Les sessions d'administration ouvertes sont fermées immédiatement : une
    révocation qui n'agirait qu'à l'expiration ne serait pas une révocation.
    """
    return grant(email, main.SECURITY_ROLE_MEMBER)


def list_admins() -> int:
    with main.db() as conn:
        rows = conn.execute(
            "SELECT email, security_role FROM users WHERE security_role IS NOT NULL "
            "AND security_role != ? ORDER BY email",
            (main.SECURITY_ROLE_MEMBER,),
        ).fetchall()
    if not rows:
        print("aucun administrateur")
        return 0
    for row in rows:
        print(f"{row['security_role']:<20} {row['email']}")
    return 0


def run(argv: list[str]) -> int:
    if not argv:
        _print_usage()
        return 2
    command, *rest = argv
    if command == "grant" and len(rest) == 2:
        return grant(rest[0], rest[1])
    if command == "revoke" and len(rest) == 1:
        return revoke(rest[0])
    if command == "list" and not rest:
        return list_admins()
    _print_usage()
    return 2


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
