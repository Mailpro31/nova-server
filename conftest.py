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
