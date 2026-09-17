-- 0007 organization_group_source
--
-- D'ou vient un groupe.
--
-- `scim` : cree par l'annuaire, qui en reste le seul auteur. La console le
-- montre et s'en sert pour viser des lecons ou des reglages, sans le modifier.
--
-- `console` : cree dans Nova Admin, pour une organisation sans annuaire. Le
-- client SCIM ne le voit jamais : il ne doit ni le modifier ni le supprimer.
--
-- Les groupes existants venaient tous de SCIM.
--
-- Dialecte : TEXT, REAL et INTEGER seulement, comme la baseline.

ALTER TABLE organization_groups ADD COLUMN source TEXT NOT NULL DEFAULT 'scim';
