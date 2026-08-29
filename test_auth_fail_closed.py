"""En production, désactiver l'authentification ne désactive rien.

## Ce que ce fichier prouve

`main.py` porte un raccourci de développement : quand `NOVA_REQUIRE_AUTH` ne
vaut pas `true`, `current_user()` renvoie l'identité `dev` sans lire le moindre
en-tête. Un audit a montré qu'avec cette seule variable, un déploiement de
production servait `/api/me`, `/api/vocabulary`, `/api/learning/progress` et
`/api/organization/policy` à n'importe qui, sans jeton.

`production_config` refuse désormais la variable au démarrage. Ce fichier
vérifie la **seconde** barrière : celle qui tient même quand personne n'est
passé par le validateur — un worker lancé à la main, un import depuis un script
d'exploitation, un entrypoint de conteneur raccourci.

## Pourquoi des sous-processus

`REQUIRE_AUTH` est calculé à l'import de `main`. Le vérifier demande donc un
interpréteur neuf par cas : recharger le module au milieu de la suite
remplacerait `main.app` sous les pieds des autres tests. Chaque sonde reçoit sa
propre base temporaire, et aucune n'écrit ailleurs.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest


# La sonde tourne dans un interpréteur neuf : elle importe `main`, construit un
# client de test, et frappe des routes membre **sans en-tête d'autorisation**.
PROBE = r"""
import json, os, sys, tempfile
os.environ["NOVA_DB"] = os.path.join(tempfile.mkdtemp(prefix="nova-failclosed-"), "probe.db")
os.environ.setdefault("NOVA_DOMAINS", "example.com")
os.environ.setdefault("NOVA_ORGANIZATION_NAME", "Probe Organization")
os.environ["NOVA_ENTRA_CLIENT_ID"] = ""
sys.path.insert(0, PROJECT)

from fastapi.testclient import TestClient
import main

main.init_db()
client = TestClient(main.app)

ROUTES = (
    "/api/me",
    "/api/vocabulary",
    "/api/learning/progress",
    "/api/organization/policy",
    "/api/learning/catalog",
)
print("NOVA_PROBE_RESULT " + json.dumps({
    "require_auth": main.REQUIRE_AUTH,
    "environment": main.NOVA_ENV,
    "statuses": {route: client.get(route).status_code for route in ROUTES},
}))
"""

PROJECT = os.path.dirname(os.path.abspath(__file__))


def probe(**overrides) -> dict:
    """Lance une sonde et renvoie ce qu'elle a observé."""
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("NOVA_")
    }
    environment.update(overrides)
    # Le chemin du projet est injecté plutôt qu'écrit en dur dans la sonde :
    # le test doit fonctionner depuis n'importe quel répertoire courant.
    script = f"PROJECT = {PROJECT!r}\n" + PROBE
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, env=environment, cwd=tempfile.gettempdir(),
    )
    for line in completed.stdout.splitlines():
        if line.startswith("NOVA_PROBE_RESULT "):
            return json.loads(line.removeprefix("NOVA_PROBE_RESULT "))
    raise AssertionError(
        f"sonde sans résultat (code {completed.returncode})\n"
        f"stdout: {completed.stdout[-2000:]}\nstderr: {completed.stderr[-2000:]}"
    )


# Routes qui exigent une identité. `/api/learning/catalog` en fait partie : le
# catalogue est statique, mais il n'a pas à être lisible sans compte.
GUARDED = (
    "/api/me",
    "/api/vocabulary",
    "/api/learning/progress",
    "/api/organization/policy",
    "/api/learning/catalog",
)


class ProductionFailsClosedTests(unittest.TestCase):
    def test_disabling_authentication_has_no_effect_in_production(self):
        result = probe(NOVA_ENV="production", NOVA_REQUIRE_AUTH="false")
        self.assertTrue(
            result["require_auth"],
            "REQUIRE_AUTH doit rester vrai en production, quoi qu'annonce la variable",
        )
        for route in GUARDED:
            with self.subTest(route=route):
                self.assertEqual(
                    401, result["statuses"][route],
                    f"{route} a répondu {result['statuses'][route]} sans jeton",
                )

    def test_every_falsy_spelling_is_ignored_in_production(self):
        # `main.py` compare à `"true"` : `1` et `yes` désactivaient donc aussi
        # l'authentification. Aucune de ces valeurs ne doit rien ouvrir.
        for value in ("false", "0", "no", "off", "1", "yes", "peut-etre"):
            with self.subTest(value=value):
                result = probe(NOVA_ENV="production", NOVA_REQUIRE_AUTH=value)
                self.assertTrue(result["require_auth"])
                self.assertEqual(401, result["statuses"]["/api/me"])

    def test_an_undeclared_environment_is_treated_as_production(self):
        # Le défaut de `NOVA_ENV` est `production` : un déploiement qui oublie
        # de se déclarer obtient le comportement le plus prudent.
        result = probe(NOVA_REQUIRE_AUTH="false")
        self.assertEqual("production", result["environment"])
        self.assertTrue(result["require_auth"])
        self.assertEqual(401, result["statuses"]["/api/me"])

    def test_production_still_refuses_without_a_token_by_default(self):
        result = probe(NOVA_ENV="production")
        for route in GUARDED:
            with self.subTest(route=route):
                self.assertEqual(401, result["statuses"][route])


class DevelopmentKeepsItsShortcutTests(unittest.TestCase):
    """Le durcissement ne doit pas rendre le développement impraticable."""

    def test_development_still_gets_the_dev_identity(self):
        result = probe(NOVA_ENV="development", NOVA_REQUIRE_AUTH="false")
        self.assertFalse(result["require_auth"])
        self.assertEqual(200, result["statuses"]["/api/me"])

    def test_test_environment_still_gets_the_dev_identity(self):
        result = probe(NOVA_ENV="test", NOVA_REQUIRE_AUTH="false")
        self.assertFalse(result["require_auth"])
        self.assertEqual(200, result["statuses"]["/api/me"])

    def test_development_without_the_variable_still_requires_a_token(self):
        # Ne pas déclarer la variable n'ouvre rien, même en développement.
        result = probe(NOVA_ENV="development")
        self.assertTrue(result["require_auth"])
        self.assertEqual(401, result["statuses"]["/api/me"])


if __name__ == "__main__":
    unittest.main()
