"""Infrastructure de test partagée.

Un seul rôle pour l'instant : remettre le compteur de limitation à zéro entre
les tests.

## Pourquoi c'est nécessaire, et pourquoi ce n'est pas un contournement

Le limiteur est désormais réel. Une suite qui appelle cinquante fois
`/api/auth/verify` pour la même adresse dépasse un quota de vingt par minute —
ce qui est exactement ce que le quota doit faire en production. Sans remise à
zéro, les tests mesureraient la limitation au lieu de mesurer ce qu'ils
vérifient.

Ce que cette fixture ne fait **pas** : désactiver la limitation. Les quotas
restent appliqués à l'intérieur de chaque test, et les tests qui vérifient la
limitation elle-même fonctionnent normalement. Elle remet simplement le compteur
à zéro entre deux tests, comme une base de test repart d'un état connu.
"""

import os
import sys

import pytest


@pytest.fixture(autouse=True)
def reset_rate_limiter_between_tests():
    """Remet le compteur à zéro avant chaque test, s'il en existe un."""
    _reset()
    yield


def _reset() -> None:
    # `sys.modules` plutôt qu'un `import main` : importer `main` ici le ferait
    # charger pour *tous* les fichiers de test, y compris ceux qui n'en veulent
    # pas — et un import de `main` ouvre la base que la configuration désigne.
    module = sys.modules.get("main")
    if module is None:
        return
    reset = getattr(module, "reset_rate_limiter", None)
    if reset is not None:
        reset()
    limiter = getattr(module, "rate_limiter", None)
    if limiter is not None:
        try:
            limiter().reset()
        except Exception:
            # Un backend injoignable ne doit pas faire échouer la préparation
            # d'un test qui ne s'intéresse pas à la limitation.
            pass


# ── Dependances de test declarees dans un fichier ───────────────────────────
#
# Les suites qui exigent un vrai PostgreSQL, un vrai Redis ou un vrai runtime
# d'inference se declarent par variable d'environnement, et se sautent
# proprement quand elle est absente. Le saut est le bon comportement -- une
# machine sans PostgreSQL ne doit pas voir cinquante-six echecs -- mais il rend
# l'oubli invisible : la campagne annonce « 0 echec » en n'ayant rien execute
# de ce qui touche ces dependances.
#
# Le cas s'est produit plusieurs fois : cinq exercices de sauvegarde rapportes
# comme une limite de la machine, alors que `NOVA_PG_COMMAND_PREFIX` -- que le
# message d'erreur du produit nommait pourtant -- n'avait pas ete pose. Poser
# quatre variables de memoire a chaque lancement n'est pas une methode.
#
# `nova-test.env` les porte, a cote de ce fichier. Il n'est pas suivi par Git :
# il contient des adresses de machine, parfois un mot de passe de base de test.
# Voir `nova-test.env.example`.
#
# L'environnement reel garde la priorite : une variable deja posee -- par une
# integration continue, par un lancement ponctuel -- n'est jamais ecrasee par
# le fichier.

def _load_test_environment() -> None:
    from pathlib import Path

    fichier = Path(__file__).resolve().parent / "nova-test.env"
    if not fichier.exists():
        return
    for ligne in fichier.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        nom, _, valeur = ligne.partition("=")
        nom, valeur = nom.strip(), valeur.strip().strip('"').strip("'")
        # Ce fichier ne configure que les tests. Une variable de production
        # posee ici passerait inapercue et changerait ce que la suite mesure.
        if not nom.startswith(("NOVA_TEST_", "NOVA_PG_")):
            continue
        os.environ.setdefault(nom, valeur)


_load_test_environment()
