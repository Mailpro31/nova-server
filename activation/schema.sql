-- Un code d'activation = une ligne. Jamais le code en clair, jamais de clé.
CREATE TABLE IF NOT EXISTS codes (
  code_hash  TEXT PRIMARY KEY,      -- SHA-256 (hex) du code normalisé
  licence    TEXT NOT NULL,         -- licence DÉJÀ signée sur le PC de l'éditeur
  school     TEXT NOT NULL,
  expires_at INTEGER NOT NULL,      -- secondes Unix ; au-delà, le code est refusé
  used_at    INTEGER                -- NULL tant que le code n'est pas utilisé
);
-- Limite de débit par IP : fenêtre fixe d'une minute.
CREATE TABLE IF NOT EXISTS hits (
  ip     TEXT NOT NULL,
  minute INTEGER NOT NULL,
  n      INTEGER NOT NULL,
  PRIMARY KEY (ip, minute)
);
