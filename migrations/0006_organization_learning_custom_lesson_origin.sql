-- 0006 organization_learning_custom_lesson_origin
--
-- D'ou vient une lecon de l'organisation.
--
-- ## Une copie personnalisee garde son origine
--
-- Personnaliser une lecon livree en fait une lecon de l'organisation. Ces deux
-- colonnes retiennent la lecon livree copiee et la version copiee. Quand Nova
-- livre une version plus recente, la console le signale ; la copie ne change
-- que si l'administrateur applique la mise a jour. Garder sa version avance
-- `based_on_version` sans toucher au contenu.
--
-- Une lecon ecrite depuis zero laisse les deux colonnes a NULL.
--
-- Dialecte : TEXT, REAL et INTEGER seulement, comme la baseline.

ALTER TABLE organization_learning_custom_lessons ADD COLUMN based_on_lesson_id TEXT;
ALTER TABLE organization_learning_custom_lessons ADD COLUMN based_on_version INTEGER;
