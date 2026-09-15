-- 0005 organization_group_policies
--
-- Les reglages qu'un groupe surcharge pour ses membres.
--
-- ## Des surcharges explicites
--
-- Contrairement a `organization_policies`, qui ne garde que ce qui differe des
-- defauts, cette table garde chaque valeur choisie pour le groupe, meme egale
-- au defaut : pour un groupe, `true` peut rouvrir ce que l'organisation a
-- ferme. Un document vide rend le groupe aux reglages de l'organisation.
--
-- Une ligne n'est jamais supprimee quand ses surcharges sont effacees : sa
-- revision continue d'avancer, et le repere de policy des membres bouge a
-- chaque ecriture.
--
-- Dialecte : TEXT, REAL et INTEGER seulement, comme la baseline.

CREATE TABLE organization_group_policies(
    organization_id    TEXT NOT NULL,
    group_id           TEXT NOT NULL,
    revision           INTEGER NOT NULL,
    settings           TEXT NOT NULL,
    updated_at         REAL NOT NULL,
    updated_by_user_id TEXT,
    PRIMARY KEY (organization_id, group_id)
);
