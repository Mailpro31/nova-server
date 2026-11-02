-- 0012 client_versions
--
-- La version de Nova Desktop que chaque membre utilise, telle que son poste
-- l'annonce à `/api/me`. Une ligne par membre, remplacée à chaque contact :
-- la console sait ainsi combien de postes restent sur une ancienne version,
-- sans garder d'historique.
--
-- Rien d'autre : ni adresse, ni nom de machine, ni système. La version suffit
-- à savoir qui relancer ; le reste relèverait de l'inventaire du poste.

CREATE TABLE client_versions(
    user_id          TEXT NOT NULL,
    organization_id  TEXT,
    version          TEXT NOT NULL,
    seen_at          TEXT NOT NULL,
    PRIMARY KEY (user_id)
);

CREATE INDEX idx_client_versions_organization ON client_versions(organization_id);
