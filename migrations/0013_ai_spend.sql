-- 0013 ai_spend
--
-- La dépense d'IA externe (2.3), en euros, par mois et par personne. Elle sert
-- à tenir l'enveloppe du contrat et le plafond doux de chacun ; la console n'en
-- montre que des totaux, sans nom.

CREATE TABLE IF NOT EXISTS ai_spend (
    month TEXT NOT NULL,
    email TEXT NOT NULL,
    eur REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (month, email)
);
