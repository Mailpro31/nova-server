// Licences signées par la console : validation, signature EdDSA, codes d'activation.
import { b64url, encoder, fromB64url } from "./b64";

const CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ";
const KINDS = ["pilot", "subscription"];

// ------------------------------------------------------------ licence

export interface LicenceRequest {
  organization: string;
  tier: string;
  kind: string;
  starts: string;
  ends: string;
  ai_envelope_eur?: number;
}

// Refuse aussi le 31 février, que Date ferait glisser au 3 mars.
const isDay = (day: string) => {
  const parsed = new Date(`${day}T00:00:00Z`);
  return /^\d{4}-\d{2}-\d{2}$/.test(day) && !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === day;
};

/** Mêmes règles que licence.issue() côté serveur ; null si tout va bien. */
export function invalid(r: LicenceRequest): string | null {
  if (!r.organization.trim() || r.organization.length > 100) return "Nom de l'établissement manquant.";
  if (!r.tier.trim() || r.tier.length > 40) return "Offre manquante.";
  if (!KINDS.includes(r.kind)) return "Type inconnu.";
  if (!isDay(r.starts) || !isDay(r.ends)) return "Dates au format AAAA-MM-JJ.";
  if (r.ends < r.starts) return "La fin est avant le début.";
  if (r.ai_envelope_eur !== undefined && !(Number.isFinite(r.ai_envelope_eur) && r.ai_envelope_eur >= 0)) return "Enveloppe d'IA invalide (nombre positif attendu).";
  return null;
}

/** Jeton EdDSA lisible par PyJWT (licence.licence_state). */
export async function signLicence(pem: string, r: LicenceRequest): Promise<string> {
  const der = fromB64url(pem.replace(/-----[^-]+-----/g, "").replace(/\s+/g, ""));
  const key = await crypto.subtle.importKey("pkcs8", der, { name: "Ed25519" }, false, ["sign"]);
  const claims: Record<string, unknown> = {
    organization: r.organization.trim(), tier: r.tier.trim(), kind: r.kind, starts: r.starts, ends: r.ends,
  };
  if (r.ai_envelope_eur !== undefined) claims.ai_envelope_eur = r.ai_envelope_eur;
  const input = `${b64url(encoder.encode(JSON.stringify({ alg: "EdDSA", typ: "JWT" })))}.${b64url(encoder.encode(JSON.stringify(claims)))}`;
  const signature = new Uint8Array(await crypto.subtle.sign("Ed25519", key, encoder.encode(input)));
  return `${input}.${b64url(signature)}`;
}

/** « NOVA-7K2P-91XQ », tiré sans biais (rejet des octets ≥ 248). */
export function newCode(): string {
  let chars = "";
  while (chars.length < 8) {
    for (const byte of crypto.getRandomValues(new Uint8Array(16))) {
      if (byte < 248 && chars.length < 8) chars += CODE_ALPHABET[byte % CODE_ALPHABET.length];
    }
  }
  return `NOVA-${chars.slice(0, 4)}-${chars.slice(4)}`;
}

/** Contenu (claims) d'une licence signée, sans vérifier la signature : pour l'affichage. */
export const claimsOf = (licence: string): Record<string, unknown> => {
  try {
    return JSON.parse(new TextDecoder().decode(fromB64url(licence.split(".")[1])));
  } catch {
    return {};
  }
};

