"""Regles de tri automatique : « si le service vaut Aero 2, alors groupe Aero 2 ».

Beaucoup d'annuaires n'ont pas de groupes propres, seulement des attributs par
personne. Une regle range chacun dans un groupe de la console :

- champs : service, organisation, domaine de l'adresse, type de membre ;
- operateurs : est egal a, contient, commence par — sans tenir compte de la
  casse ni des espaces autour ;
- appliquee a la creation ou a la modification d'une regle, a chaque
  provisioning SCIM, a l'ajout d'un membre en console, et sur demande ;
- quelqu'un qui ne correspond plus sort du groupe ; un ajout fait a la main
  n'est jamais retire par une regle ;
- seuls les groupes de la console se remplissent par regle : un groupe de
  l'annuaire reste a l'annuaire ;
- chaque ecriture est tracee, et un apercu dit qui serait range avant
  d'enregistrer.
"""

import unittest
import uuid

from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()

SCIM = {"Content-Type": "application/scim+json"}
ENTERPRISE = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"


def clear():
    with main.db() as conn:
        for table in ("organization_group_rules", "organization_group_memberships",
                      "organization_group_policies", "organization_groups",
                      "scim_user_management", "scim_credentials"):
            conn.execute(f"DELETE FROM {table}")


class GroupRulesTestCase(AdminTestCase):
    def setUp(self):
        super().setUp()
        clear()
        self.organization = main.nova_organization_id()
        self.admin = self.admin_session()

    # -- aides -------------------------------------------------------------

    def base(self) -> str:
        return f"/api/control/organizations/{self.organization}"

    def group(self, name: str) -> str:
        response = self.client.post(f"{self.base()}/groups", headers=self.auth(self.admin),
                                    json={"display_name": name})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["group"]["id"]

    def directory_group(self, name: str) -> str:
        group_id = str(uuid.uuid4())
        with main.db() as conn:
            conn.execute(
                "INSERT INTO organization_groups(organization_id, group_id, external_id, "
                "display_name, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (self.organization, group_id, f"ext-{group_id}", name, 1.0, 1.0))
        return group_id

    def person(self, email: str, department: str | None = None,
               organization: str | None = None, role: str | None = None) -> str:
        self.sign_in(email)
        with main.db() as conn:
            user_id = main.user_by_email(conn, self.organization, email)["user_id"]
            if role:
                conn.execute("UPDATE users SET role=? WHERE user_id=?", (role, user_id))
            if department is not None or organization is not None:
                conn.execute(
                    "INSERT INTO scim_user_management(organization_id, user_id, user_name, "
                    "normalized_user_name, department, enterprise_organization, created_at, "
                    "updated_at, last_provisioned_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (self.organization, user_id, email, email, department, organization,
                     1.0, 1.0, 1.0))
        return user_id

    def create_rule(self, group_id: str, field: str = "department", operator: str = "equals",
                    value: str = "Aero 2", **extra):
        return self.client.post(f"{self.base()}/group-rules", headers=self.auth(self.admin),
                                json={"group_id": group_id, "field": field,
                                      "operator": operator, "value": value, **extra})

    def rule(self, group_id: str, **kwargs) -> dict:
        response = self.create_rule(group_id, **kwargs)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def members(self, group_id: str) -> set[str]:
        response = self.client.get(f"{self.base()}/groups/{group_id}/members",
                                   headers=self.auth(self.admin))
        self.assertEqual(response.status_code, 200, response.text)
        return {member["email"] for member in response.json()["members"]}

    def audit_actions(self) -> list[str]:
        with main.db() as conn:
            return [row["action"] for row in conn.execute(
                "SELECT action FROM admin_audit_log WHERE action LIKE 'group_rule.%'")]


class MatchingTests(GroupRulesTestCase):
    def test_a_rule_sorts_existing_members_when_it_is_created(self):
        self.person("a@example.com", department="Aero 2")
        self.person("b@example.com", department="Aero 3")
        aero2 = self.group("Aero 2")
        body = self.rule(aero2)
        self.assertEqual(body["applied"], {"added": 1, "removed": 0})
        self.assertEqual(self.members(aero2), {"a@example.com"})

    def test_matching_ignores_case_and_surrounding_spaces(self):
        self.person("a@example.com", department="  aero 2 ")
        aero2 = self.group("Aero 2")
        self.rule(aero2, value="AERO 2")
        self.assertEqual(self.members(aero2), {"a@example.com"})

    def test_contains_and_starts_with(self):
        self.person("a@example.com", organization="IPSA Toulouse")
        self.person("b@example.com", organization="IPSA Paris")
        toulouse = self.group("Toulouse")
        ipsa = self.group("IPSA")
        self.rule(toulouse, field="organization", operator="contains", value="toulouse")
        self.rule(ipsa, field="organization", operator="starts_with", value="ipsa")
        self.assertEqual(self.members(toulouse), {"a@example.com"})
        self.assertEqual(self.members(ipsa), {"a@example.com", "b@example.com"})

    def test_email_domain_and_member_type(self):
        self.person("prof@example.com", role="teacher")
        self.person("eleve@example.com", role="student")
        teachers = self.group("Enseignants")
        everyone = self.group("Domaine")
        self.rule(teachers, field="member_type", value="teacher")
        self.rule(everyone, field="email_domain", value="example.com")
        self.assertEqual(self.members(teachers), {"prof@example.com"})
        self.assertTrue({"prof@example.com", "eleve@example.com"} <= self.members(everyone))

    def test_a_disabled_rule_sorts_nobody(self):
        self.person("a@example.com", department="Aero 2")
        aero2 = self.group("Aero 2")
        self.rule(aero2, enabled=False)
        self.assertEqual(self.members(aero2), set())


class LifecycleTests(GroupRulesTestCase):
    def test_someone_who_no_longer_matches_leaves_the_group(self):
        user_id = self.person("a@example.com", department="Aero 2")
        aero2 = self.group("Aero 2")
        self.rule(aero2)
        with main.db() as conn:
            conn.execute("UPDATE scim_user_management SET department='Aero 3' WHERE user_id=?",
                         (user_id,))
        response = self.client.post(f"{self.base()}/group-rules/apply",
                                    headers=self.auth(self.admin))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["applied"], {"added": 0, "removed": 1})
        self.assertEqual(self.members(aero2), set())

    def test_a_manual_member_is_never_removed_by_a_rule(self):
        self.person("a@example.com", department="Aero 3")
        manual = self.person("manuel@example.com", department="Aero 3")
        aero2 = self.group("Aero 2")
        response = self.client.put(f"{self.base()}/groups/{aero2}/members/{manual}",
                                   headers=self.auth(self.admin), json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.rule(aero2)
        self.client.post(f"{self.base()}/group-rules/apply", headers=self.auth(self.admin))
        self.assertEqual(self.members(aero2), {"manuel@example.com"})

    def test_editing_a_rule_resorts_everyone(self):
        self.person("a@example.com", department="Aero 2")
        self.person("b@example.com", department="Aero 3")
        group = self.group("Promo")
        rule_id = self.rule(group)["rule"]["id"]
        response = self.client.put(f"{self.base()}/group-rules/{rule_id}",
                                   headers=self.auth(self.admin),
                                   json={"group_id": group, "field": "department",
                                         "operator": "equals", "value": "Aero 3",
                                         "enabled": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["applied"], {"added": 1, "removed": 1})
        self.assertEqual(self.members(group), {"b@example.com"})

    def test_deleting_a_rule_removes_only_what_it_added(self):
        self.person("a@example.com", department="Aero 2")
        manual = self.person("manuel@example.com")
        aero2 = self.group("Aero 2")
        self.client.put(f"{self.base()}/groups/{aero2}/members/{manual}",
                        headers=self.auth(self.admin), json={})
        rule_id = self.rule(aero2)["rule"]["id"]
        response = self.client.delete(f"{self.base()}/group-rules/{rule_id}",
                                      headers=self.auth(self.admin))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["applied"], {"added": 0, "removed": 1})
        self.assertEqual(self.members(aero2), {"manuel@example.com"})

    def test_two_rules_on_one_group_keep_whoever_matches_either(self):
        self.person("a@example.com", department="Aero 2")
        self.person("b@example.com", department="Aero 2 bis")
        group = self.group("Aero 2")
        self.rule(group)
        self.rule(group, value="Aero 2 bis")
        self.assertEqual(self.members(group), {"a@example.com", "b@example.com"})

    def test_a_member_added_by_a_rule_is_listed_as_such_and_not_removed_by_hand(self):
        user_id = self.person("a@example.com", department="Aero 2")
        aero2 = self.group("Aero 2")
        self.rule(aero2)
        listed = self.client.get(f"{self.base()}/groups/{aero2}/members",
                                 headers=self.auth(self.admin)).json()["members"]
        self.assertEqual(listed[0]["origin"], "rule")
        response = self.client.delete(f"{self.base()}/groups/{aero2}/members/{user_id}",
                                      headers=self.auth(self.admin))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "GROUP_MEMBER_ADDED_BY_RULE")

    def test_deleting_a_group_deletes_its_rules(self):
        aero2 = self.group("Aero 2")
        self.rule(aero2)
        self.client.delete(f"{self.base()}/groups/{aero2}", headers=self.auth(self.admin))
        rules = self.client.get(f"{self.base()}/group-rules",
                                headers=self.auth(self.admin)).json()["rules"]
        self.assertEqual(rules, [])


class TriggerTests(GroupRulesTestCase):
    def scim_headers(self) -> dict:
        response = self.client.post(f"{self.base()}/provisioning/credentials",
                                    headers=self.auth(self.admin),
                                    json={"name": "Entra", "provider_context": "entra"})
        self.assertEqual(response.status_code, 200, response.text)
        return {**SCIM, "Authorization": f"Bearer {response.json()['token']}"}

    def scim_user(self, headers: dict, email: str, department: str) -> dict:
        response = self.client.post("/scim/v2/Users", headers=headers, json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User", ENTERPRISE],
            "userName": email,
            "externalId": f"ext-{email}",
            "active": True,
            "emails": [{"value": email, "primary": True}],
            ENTERPRISE: {"department": department},
        })
        self.assertIn(response.status_code, (200, 201), response.text)
        return response.json()

    def test_provisioning_sorts_a_new_person_immediately(self):
        aero2 = self.group("Aero 2")
        self.rule(aero2)
        self.scim_user(self.scim_headers(), "nouveau@example.com", "Aero 2")
        self.assertEqual(self.members(aero2), {"nouveau@example.com"})

    def test_a_directory_change_moves_the_person(self):
        aero2 = self.group("Aero 2")
        aero3 = self.group("Aero 3")
        self.rule(aero2)
        self.rule(aero3, value="Aero 3")
        headers = self.scim_headers()
        user = self.scim_user(headers, "promu@example.com", "Aero 2")
        response = self.client.patch(f"/scim/v2/Users/{user['id']}", headers=headers, json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": f"{ENTERPRISE}:department",
                            "value": "Aero 3"}],
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.members(aero2), set())
        self.assertEqual(self.members(aero3), {"promu@example.com"})

    def test_a_member_added_in_the_console_is_sorted(self):
        teachers = self.group("Enseignants")
        self.rule(teachers, field="member_type", value="teacher")
        response = self.client.post(f"{self.base()}/members", headers=self.auth(self.admin),
                                    json={"email": "prof@example.com", "member_role": "teacher"})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.members(teachers), {"prof@example.com"})


class PreviewAndValidationTests(GroupRulesTestCase):
    def test_the_preview_counts_and_samples_without_writing(self):
        for index in range(7):
            self.person(f"e{index}@example.com", department="Aero 2")
        aero2 = self.group("Aero 2")
        response = self.client.post(f"{self.base()}/group-rules/preview",
                                    headers=self.auth(self.admin),
                                    json={"field": "department", "operator": "equals",
                                          "value": "aero 2"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["count"], 7)
        self.assertEqual(len(response.json()["sample"]), 5)
        self.assertEqual(self.members(aero2), set())

    def test_a_rule_only_fills_a_console_group(self):
        directory = self.directory_group("Annuaire")
        response = self.create_rule(directory)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "GROUP_MANAGED_BY_DIRECTORY")
        response = self.create_rule("inconnu")
        self.assertEqual(response.status_code, 404)

    def test_fields_operators_and_values_are_checked(self):
        group = self.group("Aero 2")
        cases = [
            ({"field": "password"}, "RULE_FIELD_INVALID"),
            ({"operator": "regex"}, "RULE_OPERATOR_INVALID"),
            ({"value": "   "}, "RULE_VALUE_INVALID"),
            ({"value": "x" * 201}, "RULE_VALUE_INVALID"),
        ]
        for change, code in cases:
            response = self.create_rule(group, **{"field": "department", "operator": "equals",
                                                  "value": "Aero 2", **change})
            self.assertEqual(response.status_code, 400, (change, response.text))
            self.assertEqual(response.json()["detail"], code)

    def test_the_list_describes_each_rule_and_its_group(self):
        group = self.group("Aero 2")
        self.rule(group)
        body = self.client.get(f"{self.base()}/group-rules", headers=self.auth(self.admin)).json()
        [rule] = body["rules"]
        self.assertEqual(rule["group_label"], "Aero 2")
        self.assertEqual((rule["field"], rule["operator"], rule["value"], rule["enabled"]),
                         ("department", "equals", "Aero 2", True))
        self.assertEqual(set(body["fields"]),
                         {"department", "organization", "email_domain", "member_type"})
        self.assertEqual(set(body["operators"]), {"equals", "contains", "starts_with"})

    def test_writes_need_identity_management_and_are_audited(self):
        group = self.group("Aero 2")
        member = self.sign_in("curieux@example.com")
        response = self.client.post(f"{self.base()}/group-rules", headers=self.auth(member),
                                    json={"group_id": group, "field": "department",
                                          "operator": "equals", "value": "Aero 2"})
        self.assertIn(response.status_code, (401, 403))
        rule_id = self.rule(group)["rule"]["id"]
        self.client.put(f"{self.base()}/group-rules/{rule_id}", headers=self.auth(self.admin),
                        json={"group_id": group, "field": "department", "operator": "equals",
                              "value": "Aero 3", "enabled": False})
        self.client.post(f"{self.base()}/group-rules/apply", headers=self.auth(self.admin))
        self.client.delete(f"{self.base()}/group-rules/{rule_id}", headers=self.auth(self.admin))
        self.assertEqual(sorted(set(self.audit_actions())),
                         ["group_rule.apply", "group_rule.create", "group_rule.delete",
                          "group_rule.update"])

    def test_an_unknown_rule_is_not_found(self):
        response = self.client.delete(f"{self.base()}/group-rules/inconnu",
                                      headers=self.auth(self.admin))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "RULE_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
