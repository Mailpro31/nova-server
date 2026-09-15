/**
 * Ce qu'une ligne du journal dit avoir changé.
 *
 * Deux formes existent dans le journal. Les écritures récentes portent
 * `before` et `after` : on en tire une ligne par valeur modifiée. La policy de
 * l'organisation ne garde que `changed_keys`, les noms sans les valeurs — un
 * choix du serveur, que la console respecte en nommant les réglages sans
 * inventer ce qu'ils valaient.
 */

import { t } from "./messages";

export interface AuditChange {
  field: string;
  before: unknown;
  after: unknown;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function same(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

export function auditChanges(metadata: Record<string, unknown> | null): AuditChange[] {
  if (!metadata) return [];
  if ("before" in metadata || "after" in metadata) {
    const before = isRecord(metadata.before) ? metadata.before : null;
    const after = isRecord(metadata.after) ? metadata.after : null;
    if (!before && !after) return [];
    const fields = [...new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})])].sort();
    return fields
      .filter((field) => !same(before?.[field], after?.[field]))
      .map((field) => ({
        field,
        before: before ? before[field] : undefined,
        after: after ? after[field] : undefined,
      }));
  }
  if (Array.isArray(metadata.changed_keys)) {
    return metadata.changed_keys
      .filter((key): key is string => typeof key === "string")
      .map((field) => ({ field, before: undefined, after: undefined }));
  }
  return [];
}

export function formatAuditValue(value: unknown): string {
  if (value === undefined || value === null) return t.security.auditNoValue;
  if (value === true) return t.security.auditEnabled;
  if (value === false) return t.security.auditDisabled;
  if (Array.isArray(value)) {
    return value.length === 0 ? t.security.auditNoValue : value.map(formatAuditValue).join(", ");
  }
  if (isRecord(value)) {
    return Object.entries(value)
      .map(([key, entry]) => `${key}: ${formatAuditValue(entry)}`)
      .join(", ");
  }
  return String(value);
}
