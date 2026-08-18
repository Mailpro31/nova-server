"""Garde-fou de recette : refuse d'ouvrir la base reelle.

## Pourquoi ce fichier existe

Pendant la Phase 30, deux verifications en une ligne ont ete lancees ainsi :

    python -c "import main; ..."

Sans `NOVA_DB`, `main.py` ouvre `nova.db` — la base reelle — et `init_db()`
s'execute **a l'import**. La migration additive s'est donc appliquee a une base
de production, alors que toute la recette etait censee se derouler sur une
copie.

La lecon n'est pas « faire attention ». C'est que **`import main` est une
operation potentiellement mutante**, et qu'une regle qui repose sur la vigilance
finit toujours par ceder un jour de fatigue. D'ou un garde-fou qui refuse plutot
qu'un rappel qui previent.

## Usage

    python -c "import recipe_env; import main; ..."

ou, dans un script de recette :

    import recipe_env  # avant tout import de main
    import main

L'import de ce module leve `RuntimeError` si `NOVA_DB` n'est pas defini, ou
s'il designe une base qui n'est pas manifestement temporaire. Aucune recette ne
peut donc demarrer sur la base reelle par distraction.
"""

import os
import tempfile
from pathlib import Path


class RealDatabaseRefused(RuntimeError):
    """La recette a tente d'ouvrir une base qui n'est pas une copie."""


def _temporary_roots() -> list[Path]:
    roots = [Path(tempfile.gettempdir()).resolve()]
    for variable in ("TMP", "TEMP", "TMPDIR"):
        value = os.getenv(variable)
        if value:
            roots.append(Path(value).resolve())
    return roots


def require_temporary_database() -> Path:
    """Verifie que `NOVA_DB` designe une copie, et la renvoie.

    Deux controles, et le second est celui qui compte : une variable definie
    ne prouve rien si elle pointe vers le fichier de production.
    """
    raw = os.getenv("NOVA_DB", "").strip()
    if not raw:
        raise RealDatabaseRefused(
            "NOVA_DB n'est pas defini. Une recette ne s'execute jamais sur la "
            "base reelle : passez NOVA_DB=<copie temporaire>."
        )

    path = Path(raw).resolve()
    if path == (Path.cwd() / "nova.db").resolve():
        raise RealDatabaseRefused(
            f"NOVA_DB designe la base reelle ({path}). Faites-en une copie "
            "avec sqlite3.backup() et pointez NOVA_DB dessus."
        )

    if not any(str(path).startswith(str(root)) for root in _temporary_roots()):
        raise RealDatabaseRefused(
            f"NOVA_DB ({path}) n'est pas dans un repertoire temporaire. "
            "Refuse par prudence : une recette qui ecrit ailleurs finit par "
            "ecrire quelque part d'important."
        )
    return path


DATABASE = require_temporary_database()
