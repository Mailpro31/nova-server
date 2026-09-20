"""L'image contient tout ce que ses processus importent.

## Pourquoi ce test existe

Le `Dockerfile` ne copie pas le dépôt : il copie une **liste explicite** de
fichiers. C'est délibéré — l'image ne doit pas embarquer les tests, les
sauvegardes ni la console — mais cette liste est écrite à la main, et rien ne la
reliait au code. Un module ajouté à `main.py` sans être ajouté au `Dockerfile`
produit une image qui se construit sans erreur, passe la CI, et meurt au premier
démarrage sur `ModuleNotFoundError`.

C'est arrivé : `google_directory`, `group_rules` et `directory_worker` sont
arrivés avec les règles de tri, la synchronisation d'annuaire et l'import de
membres, et l'image publiée ne pouvait plus faire `import main`. Le serveur
n'était plus déployable, et aucun test ne le disait.

Le test compare donc deux choses calculées, jamais devinées : la fermeture des
imports internes des points d'entrée, et ce que le `Dockerfile` copie
réellement.
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Ce que le `CMD` de l'image et les `command:` du compose démarrent vraiment,
# plus l'outil d'opérateur, exécuté à la main dans le conteneur `api`.
ENTRYPOINTS = (
    "serve.py",
    "main.py",
    "connector.py",
    "directory_worker.py",
    "admin_cli.py",
)


def repository_modules() -> dict[str, Path]:
    """Modules du dépôt, hors tests : ceux qu'une image peut avoir à copier."""
    modules: dict[str, Path] = {}
    for path in ROOT.glob("*.py"):
        if path.name.startswith("test_") or path.name == "conftest.py":
            continue
        modules[path.stem] = path
    for package in ROOT.glob("*/__init__.py"):
        modules[package.parent.name] = package.parent
    return modules


def imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # `from . import x` n'existe pas à la racine ; seul le niveau 0 y
            # désigne un module du dépôt.
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def import_closure() -> set[str]:
    """Modules du dépôt réellement atteignables depuis les points d'entrée."""
    modules = repository_modules()
    seen: set[str] = set()
    queue = [name for name in (Path(entry).stem for entry in ENTRYPOINTS)]
    while queue:
        name = queue.pop()
        if name in seen or name not in modules:
            continue
        seen.add(name)
        target = modules[name]
        sources = [target] if target.is_file() else sorted(target.rglob("*.py"))
        for source in sources:
            queue.extend(imported_names(source) & modules.keys())
    return seen


def copied_by_dockerfile() -> set[str]:
    """Ce que les instructions `COPY` du `Dockerfile` déposent dans l'image."""
    copied: set[str] = set()
    for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines():
        match = re.match(r"\s*COPY\s+(.+)", line)
        if not match:
            continue
        # La dernière valeur est la destination, pas une source.
        for source in match.group(1).split()[:-1]:
            copied.add(source.rstrip("/").split("/")[-1].removesuffix(".py"))
    return copied


def test_dockerfile_copies_every_module_the_image_imports():
    missing = sorted(import_closure() - copied_by_dockerfile())
    assert not missing, (
        "Ces modules sont importés par les processus de l'image mais ne sont pas "
        f"copiés par le Dockerfile : {', '.join(missing)}. "
        "L'image se construirait, puis mourrait sur ModuleNotFoundError."
    )


def test_entrypoints_exist():
    """Un point d'entrée renommé rendrait le test ci-dessus vide, donc muet."""
    absent = [entry for entry in ENTRYPOINTS if not (ROOT / entry).is_file()]
    assert not absent, f"Points d'entrée introuvables : {', '.join(absent)}"
