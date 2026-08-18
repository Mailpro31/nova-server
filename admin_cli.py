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

    python admin_cli.py grant  <email> <role> [--organization <slug|id>]
    python admin_cli.py revoke <email>        [--organization <slug|id>]
    python admin_cli.py list                  [--organization <slug|id>]

Sans `--organization`, l'organisation servie par l'instance. En mode
control_plane, l'option devient obligatoire : une adresse ne désigne une
personne qu'à l'intérieur d'une organisation.

Rôles : organization_admin, it_admin, read_only, member.
"""

import sys

import main


def _print_usage():
    print(__doc__.strip())


def resolve_organization(reference: str | None) -> str:
    """Identifiant d'organisation, à partir d'un slug ou d'un identifiant.

    Sans argument, l'organisation servie par cette instance — ce qui est le cas
    normal en mode dédié. En mode Control Plane, l'argument devient
    indispensable : « le premier alex@example.com trouvé » n'est pas une
    personne, c'est une coïncidence.
    """
    if not reference:
        if main.NOVA_SERVER_MODE == main.SERVER_MODE_CONTROL_PLANE:
            raise LookupError(
                "--organization est obligatoire en mode control_plane"
            )
        return main.nova_organization_id()
    with main.db() as conn:
        row = conn.execute(
            "SELECT id FROM organizations WHERE id=? OR slug=? OR discovery_slug=?",
            (reference, reference, reference),
        ).fetchone()
    if row is None:
        raise LookupError(f"organisation inconnue : {reference}")
    return row["id"]


def grant(email: str, role: str, organization: str | None = None) -> int:
    try:
        organization_id = resolve_organization(organization)
        result = main.set_security_role(email, role, organization_id)
    except (ValueError, LookupError) as error:
        print(f"refusé : {error}")
        return 1
    # Pas de fleche Unicode : la console Windows en cp1252 ne sait pas l'encoder.
    print(f"{result['email']} : {result['security_role']}")
    return 0


def revoke(email: str, organization: str | None = None) -> int:
    """Retire tout privilège d'administration, sans toucher au compte.

    Les sessions d'administration ouvertes sont fermées immédiatement : une
    révocation qui n'agirait qu'à l'expiration ne serait pas une révocation.
    """
    return grant(email, main.SECURITY_ROLE_MEMBER, organization)


def list_admins(organization: str | None = None) -> int:
    try:
        organization_id = resolve_organization(organization)
    except LookupError as error:
        print(f"refusé : {error}")
        return 1
    with main.db() as conn:
        rows = conn.execute(
            "SELECT email, security_role FROM users WHERE organization_id=? "
            "AND security_role != ? ORDER BY normalized_email",
            (organization_id, main.SECURITY_ROLE_MEMBER),
        ).fetchall()
    if not rows:
        print("aucun administrateur")
        return 0
    for row in rows:
        print(f"{row['security_role']:<20} {row['email']}")
    return 0


def _extract_option(argv: list[str], name: str) -> tuple[list[str], str | None]:
    """Retire `--name valeur` de la liste et renvoie la valeur."""
    if name not in argv:
        return argv, None
    index = argv.index(name)
    if index + 1 >= len(argv):
        raise ValueError(f"{name} attend une valeur")
    return argv[:index] + argv[index + 2:], argv[index + 1]


def run(argv: list[str]) -> int:
    if not argv:
        _print_usage()
        return 2
    try:
        argv, organization = _extract_option(list(argv), "--organization")
    except ValueError as error:
        print(f"refusé : {error}")
        return 2
    command, *rest = argv
    if command == "grant" and len(rest) == 2:
        return grant(rest[0], rest[1], organization)
    if command == "revoke" and len(rest) == 1:
        return revoke(rest[0], organization)
    if command == "list" and not rest:
        return list_admins(organization)
    _print_usage()
    return 2


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
