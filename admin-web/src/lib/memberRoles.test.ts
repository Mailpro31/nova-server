/**
 * Les métiers qu'un administrateur peut attribuer.
 *
 * La console se contredisait : l'aide à l'import annonçait six valeurs
 * acceptées, la liste déroulante n'en proposait que quatre. `employee` et
 * `manager` sont déclarés côté serveur depuis que Business existe, et un
 * déploiement en entreprise ne pouvait donc pas déclarer un salarié.
 *
 * Un métier ne confère aucun droit : c'est `security_role` qui décide. Les
 * ouvrir n'élargit aucun accès.
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";

function source(path: string): string {
  return readFileSync(path, "utf8");
}

const MEMBERS_PAGE = "src/pages/Members.tsx";
const MESSAGES = "src/lib/messages.ts";

/** Ce que le serveur accepte, `MEMBER_TYPE_BY_ROLE` dans `main.py`. */
const SERVER_ROLES = [
  "student",
  "teacher",
  "staff",
  "employee",
  "manager",
  "partner",
];

function declaredRoles(): string[] {
  const code = source(MEMBERS_PAGE);
  const block = code.slice(
    code.indexOf("const MEMBER_ROLES"),
    code.indexOf("] as const;", code.indexOf("const MEMBER_ROLES")),
  );
  return [...block.matchAll(/"([a-z]+)"/g)].map((match) => match[1]);
}

describe("métiers proposés par la console", () => {
  test("la liste déroulante propose ce que le serveur accepte", () => {
    expect(declaredRoles()).toEqual(SERVER_ROLES);
  });

  test("les métiers d'entreprise y figurent", () => {
    // Le défaut exact : une entreprise ne pouvait déclarer ni salarié ni
    // encadrant, alors que l'import CSV les acceptait déjà.
    expect(declaredRoles()).toContain("employee");
    expect(declaredRoles()).toContain("manager");
  });

  test("l'aide à l'import et la liste déroulante disent la même chose", () => {
    const help = source(MESSAGES);
    for (const role of declaredRoles()) {
      expect(help).toContain(role);
    }
  });
});
