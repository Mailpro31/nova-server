-- 0012 client_version
--
-- La version de Nova installee sur le poste de chaque membre, envoyee par
-- `/api/me` (en-tete `X-Nova-Version`). La console n'en montre que des
-- comptes par version : quels postes restent a mettre a jour, sans nom.

ALTER TABLE users ADD COLUMN app_version TEXT;
