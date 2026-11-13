import { createRequire } from "node:module";
import { readFileSync } from "node:fs";

// D1 factice sur le SQLite intégré à Node : exécute le vrai schema.sql, sans réseau.
const { DatabaseSync } = createRequire(import.meta.url)("node:sqlite");

export function fakeD1() {
  const db = new DatabaseSync(":memory:");
  db.exec(readFileSync(new URL("../schema.sql", import.meta.url), "utf8"));
  return {
    prepare(sql: string) {
      let args: unknown[] = [];
      const statement = {
        bind(...a: unknown[]) { args = a; return statement; },
        async first() { return db.prepare(sql).get(...args) ?? null; },
        async all() { return { results: db.prepare(sql).all(...args) }; },
        async run() { return { meta: { changes: db.prepare(sql).run(...args).changes } }; },
      };
      return statement;
    },
  } as unknown as D1Database;
}
