-- 0008 member_groups_visibility
--
-- Ce que le membre voit de ses groupes sur Nova Desktop.
--
-- `organization_groups.visible_to_members` : 0 retire ce groupe de l'affichage
-- chez tous ses membres. Le groupe continue de s'appliquer (lecons, reglages) ;
-- seul son nom n'est plus envoye au poste. Valable pour un groupe de
-- l'annuaire comme de la console : c'est un choix de Nova, pas de l'annuaire.
--
-- `organization_member_group_display` : le choix fait pour une personne. Une
-- ligne l'emporte sur le reglage de ses groupes et de l'organisation ; pas de
-- ligne, la personne suit ces reglages.
--
-- Dialecte : TEXT, REAL et INTEGER seulement, comme la baseline.

ALTER TABLE organization_groups ADD COLUMN visible_to_members INTEGER NOT NULL DEFAULT 1;

CREATE TABLE organization_member_group_display(
    organization_id    TEXT NOT NULL,
    user_id            TEXT NOT NULL,
    visible            INTEGER NOT NULL,
    updated_at         REAL NOT NULL,
    updated_by_user_id TEXT,
    PRIMARY KEY (organization_id, user_id)
);
