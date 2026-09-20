"""Le tri automatique des membres dans les groupes de la console.

Beaucoup d'annuaires n'ont pas de groupes propres : ils n'ont que des attributs
par personne — le service, l'organisation, l'adresse. Une regle traduit un
attribut en appartenance : « service = Aero 2 » range dans le groupe Aero 2.

## Ce que ce module garantit

- **Un ajout manuel n'est jamais retire.** Seules les appartenances d'origine
  `rule` sont revues ; celles qu'un administrateur a faites restent.
- **Sortir suit entrer.** Quelqu'un qui ne correspond plus a aucune regle d'un
  groupe en sort, sans quoi la promo precedente s'y accumulerait pour toujours.
- **Aucune ecriture pour rien.** Le calcul compare l'etat voulu a l'etat
  present et n'ecrit que la difference : les reperes du poste ne bougent que
  lorsque quelque chose a reellement change.
- **Ce module ne decide d'aucun droit.** Il range des personnes dans des
  groupes ; ce que ces groupes ouvrent ou ferment se decide ailleurs.

Il ne connait ni FastAPI ni les gardes d'administration : il recoit une
connexion et rend des comptes.
"""

FIELDS = ("department", "organization", "email_domain", "member_type")
OPERATORS = ("equals", "contains", "starts_with")

MAX_VALUE = 200
MAX_RULES = 200
ORIGIN_RULE = "rule"
ORIGIN_MANUAL = "manual"


def normalized(value: str | None) -> str:
    """Compare ce qui se lit, pas ce qui se tape : casse et espaces ignores."""
    return (value or "").strip().casefold()


def matches(field: str, operator: str, value: str, attributes: dict) -> bool:
    candidate = normalized(attributes.get(field))
    wanted = normalized(value)
    if not candidate or not wanted:
        return False
    if operator == "equals":
        return candidate == wanted
    if operator == "contains":
        return wanted in candidate
    if operator == "starts_with":
        return candidate.startswith(wanted)
    return False


def member_attributes(row) -> dict:
    """Les attributs d'une personne, tels que l'annuaire et le compte les portent."""
    email = (row["email"] or "") if "email" in row.keys() else ""
    _, _, domain = email.partition("@")
    return {
        "department": row["department"] if "department" in row.keys() else None,
        "organization": (
            row["enterprise_organization"] if "enterprise_organization" in row.keys() else None
        ),
        "email_domain": domain,
        "member_type": row["role"] if "role" in row.keys() else None,
    }


def members_with_attributes(conn, organization_id: str, user_id: str | None = None) -> list:
    """Les comptes de l'organisation, avec ce que l'annuaire en dit.

    Un compte deprovisionne n'est pas range : il n'appartient plus a
    l'organisation, et le provisioning a deja retire ses appartenances.
    """
    clause = " AND u.user_id=?" if user_id else ""
    parameters = (organization_id, user_id) if user_id else (organization_id,)
    return conn.execute(
        "SELECT u.user_id, u.email, u.role, u.status, m.department, m.enterprise_organization "
        "FROM users u LEFT JOIN scim_user_management m "
        "ON m.organization_id=u.organization_id AND m.user_id=u.user_id "
        f"WHERE u.organization_id=?{clause}",
        parameters,
    ).fetchall()


def active_rules(conn, organization_id: str) -> list:
    """Les regles qui rangent : activees, et visant un groupe qui existe encore."""
    return conn.execute(
        "SELECT r.* FROM organization_group_rules r "
        "JOIN organization_groups g ON g.organization_id=r.organization_id "
        "AND g.group_id=r.group_id AND g.deleted_at IS NULL "
        "WHERE r.organization_id=? AND r.enabled=1 ORDER BY r.created_at",
        (organization_id,),
    ).fetchall()


def wanted_groups(rules: list, attributes: dict) -> set[str]:
    return {
        rule["group_id"]
        for rule in rules
        if matches(rule["field"], rule["operator"], rule["value"], attributes)
    }


def apply_rules(conn, organization_id: str, user_id: str | None = None,
                now: float | None = None) -> dict:
    """Range l'organisation — ou une seule personne — et dit ce qui a bouge.

    Appelee a chaque ecriture de regle, a chaque provisioning, a l'ajout d'un
    membre en console, et sur demande depuis la console.
    """
    import time

    stamp = time.time() if now is None else now
    rules = active_rules(conn, organization_id)
    added = removed = 0
    for row in members_with_attributes(conn, organization_id, user_id):
        if (row["status"] or "active") == "deprovisioned":
            continue
        wanted = wanted_groups(rules, member_attributes(row))
        present = conn.execute(
            "SELECT group_id, origin FROM organization_group_memberships "
            "WHERE organization_id=? AND user_id=?",
            (organization_id, row["user_id"]),
        ).fetchall()
        held = {membership["group_id"] for membership in present}
        by_rule = {
            membership["group_id"]
            for membership in present
            if membership["origin"] == ORIGIN_RULE
        }
        for group_id in sorted(wanted - held):
            conn.execute(
                "INSERT INTO organization_group_memberships(organization_id, group_id, "
                "user_id, created_at, origin) VALUES (?,?,?,?,?)",
                (organization_id, group_id, row["user_id"], stamp, ORIGIN_RULE),
            )
            added += 1
        for group_id in sorted(by_rule - wanted):
            conn.execute(
                "DELETE FROM organization_group_memberships "
                "WHERE organization_id=? AND group_id=? AND user_id=?",
                (organization_id, group_id, row["user_id"]),
            )
            removed += 1
    return {"added": added, "removed": removed}


def preview(conn, organization_id: str, field: str, operator: str, value: str,
            sample_size: int = 5) -> dict:
    """Qui serait range par cette regle, avant de l'enregistrer.

    Rien n'est ecrit : un apercu qui modifierait la base ne serait pas un
    apercu.
    """
    emails = sorted(
        row["email"]
        for row in members_with_attributes(conn, organization_id)
        if (row["status"] or "active") != "deprovisioned"
        and matches(field, operator, value, member_attributes(row))
    )
    return {"count": len(emails), "sample": emails[:sample_size]}
