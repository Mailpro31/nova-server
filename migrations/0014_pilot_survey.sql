-- 0014 pilot_survey
--
-- Le mini-sondage du bilan de pilote (2.4). La note n'est jamais rangée à
-- côté d'un nom : `survey_scores` ne garde qu'une somme et un compte par mois,
-- `survey_answered` seulement qui a déjà répondu ce mois-ci, pour ne compter
-- chacun qu'une fois. Rien ne relie l'une à l'autre.

CREATE TABLE IF NOT EXISTS survey_scores (
    month TEXT PRIMARY KEY,
    total INTEGER NOT NULL DEFAULT 0,
    answers INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS survey_answered (
    email TEXT NOT NULL,
    month TEXT NOT NULL,
    PRIMARY KEY (email, month)
);
