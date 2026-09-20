-- 0009 group_rules
--
-- Les regles de tri automatique, et l'origine de chaque appartenance.
--
-- ## Pourquoi une origine
--
-- Une regle doit pouvoir retirer ce qu'elle a mis — quelqu'un qui change de
-- service quitte son ancien groupe — sans jamais retirer ce qu'un
-- administrateur a ajoute a la main. Sans cette colonne, les deux se
-- confondraient, et la premiere synchronisation effacerait le travail manuel.
--
-- `manual` par defaut : tout ce qui existe aujourd'hui a ete ajoute a la main,
-- en console ou par l'annuaire, et ne doit pas disparaitre.
--
-- ## Les regles
--
-- `field` : department | organization | email_domain | member_type.
-- `operator` : equals | contains | starts_with.
-- `group_id` designe **toujours** un groupe de la console : l'annuaire reste
-- le seul auteur de ses propres groupes. Une regle desactivee ne range
-- personne, et ce qu'elle avait range en sort.
--
-- Dialecte : TEXT, REAL et INTEGER seulement, comme la baseline.

ALTER TABLE organization_group_memberships ADD COLUMN origin TEXT NOT NULL DEFAULT 'manual';

CREATE TABLE organization_group_rules(
    organization_id    TEXT NOT NULL,
    rule_id            TEXT NOT NULL,
    group_id           TEXT NOT NULL,
    field              TEXT NOT NULL,
    operator           TEXT NOT NULL,
    value              TEXT NOT NULL,
    enabled            INTEGER NOT NULL DEFAULT 1,
    created_at         REAL NOT NULL,
    updated_at         REAL NOT NULL,
    updated_by_user_id TEXT,
    PRIMARY KEY (organization_id, rule_id)
);

CREATE INDEX idx_organization_group_rules_group
    ON organization_group_rules(organization_id, group_id);
