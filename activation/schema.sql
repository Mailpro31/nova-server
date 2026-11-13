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
-- Pilotage de Claude depuis la console (écrit par la console ou par scripts/pilot.mjs).
CREATE TABLE IF NOT EXISTS pilot_state (
  key   TEXT PRIMARY KEY,            -- 'paused' : '1' ou '0'
  value TEXT NOT NULL,
  at    INTEGER NOT NULL,
  by    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pilot_journal (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  at      INTEGER NOT NULL,
  kind    TEXT NOT NULL,             -- fusion, deploiement, publication, bug, banc, message…
  result  TEXT NOT NULL,             -- ok, echec, info
  title   TEXT NOT NULL,
  link    TEXT,
  details TEXT                       -- rapport détaillé : symptôme, journaux, cause, correctif, preuve
);
CREATE TABLE IF NOT EXISTS pilot_prs (  -- relevé des PR ouvertes, remplacé à chaque réveil
  repo    TEXT NOT NULL,
  number  INTEGER NOT NULL,
  title   TEXT NOT NULL,
  ci      TEXT NOT NULL,             -- vert, rouge, en cours, aucune
  url     TEXT NOT NULL,
  seen_at INTEGER NOT NULL,
  PRIMARY KEY (repo, number)
);
CREATE TABLE IF NOT EXISTS pilot_holds (  -- PR que Claude ne doit pas fusionner
  repo   TEXT NOT NULL,
  number INTEGER NOT NULL,
  reason TEXT,
  at     INTEGER NOT NULL,
  by     TEXT NOT NULL,
  PRIMARY KEY (repo, number)
);
CREATE TABLE IF NOT EXISTS pilot_questions (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  at          INTEGER NOT NULL,
  title       TEXT NOT NULL,
  details     TEXT,
  answer      TEXT,
  answered_at INTEGER,
  answered_by TEXT
);
CREATE TABLE IF NOT EXISTS pilot_messages (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  at         INTEGER NOT NULL,
  by         TEXT NOT NULL,
  body       TEXT NOT NULL,
  read_at    INTEGER,
  reply      TEXT,
  replied_at INTEGER
);
