"""Learn — catalogue pedagogique, validation, et regles de progression.

## Pourquoi un module a part

Learn appartient au Nova Core : il doit valoir pour Personal, Campus et
Business sans qu'aucune de ces editions ne soit citee ici. Ce module ne connait
donc ni organisation, ni edition, ni session — il ne connait qu'un catalogue et
des regles. Ce qui depend du tenant vit dans `main.py`, ou il a deja tout ce
qu'il faut pour le faire correctement.

## Le catalogue est une donnee, pas du code

`catalog.json` porte le contenu ; ce fichier le lit et le verifie. Ecrire les
lecons en JSX aurait rendu chaque correction de texte dependante d'un
deploiement d'interface, et aurait mis le contenu hors de portee de tout test.

## L'instruction d'un exercice IA ne quitte jamais le serveur

Un bloc `ai_exercise` ne transporte qu'un identifiant. L'instruction
pedagogique reste ici, exactement comme l'instruction d'un AI Skill reste dans
son package : si le poste l'envoyait, n'importe qui pourrait faire executer
n'importe quoi au modele en se reclamant d'un exercice.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

CATALOG_DIR = Path(__file__).resolve().parent
CATALOG_PATH = CATALOG_DIR / "catalog.json"

# La langue du catalogue de reference. Une traduction vit a cote de lui sous le
# nom `catalog.<langue>.json` et garde exactement le meme squelette : la
# progression est enregistree par identifiant, pas par langue.
DEFAULT_LOCALE = "en"
_LANGUAGE = re.compile(r"^[a-z]{2,3}$")

# Les trois piliers. Leurs noms internes restent stables en anglais ; ce que
# l'interface affiche vient du catalogue, et pourra venir d'une traduction.
PILLAR_USE_AI = "use_ai"
PILLAR_LEARN_AI = "learn_ai"
PILLAR_ADAPT_AI = "adapt_ai"
PILLARS = frozenset({PILLAR_USE_AI, PILLAR_LEARN_AI, PILLAR_ADAPT_AI})

# Types de blocs connus du moteur de rendu. Un type absent de cette liste fait
# echouer la validation plutot que d'atteindre un poste qui ne saura pas
# l'afficher — et qui, au mieux, n'afficherait rien.
BLOCK_TEXT = "text"
BLOCK_EXAMPLE = "example"
BLOCK_COMPARISON = "comparison"
BLOCK_TIP = "tip"
BLOCK_WARNING = "warning"
BLOCK_QUESTION = "question"
BLOCK_MULTIPLE_CHOICE = "multiple_choice"
BLOCK_REFLECTION = "reflection"
BLOCK_PROMPT_EXERCISE = "prompt_exercise"
BLOCK_AI_EXERCISE = "ai_exercise"
BLOCK_TAKEAWAY = "takeaway"

BLOCK_TYPES = frozenset(
    {
        BLOCK_TEXT,
        BLOCK_EXAMPLE,
        BLOCK_COMPARISON,
        BLOCK_TIP,
        BLOCK_WARNING,
        BLOCK_QUESTION,
        BLOCK_MULTIPLE_CHOICE,
        BLOCK_REFLECTION,
        BLOCK_PROMPT_EXERCISE,
        BLOCK_AI_EXERCISE,
        BLOCK_TAKEAWAY,
    }
)

# Blocs qu'il faut avoir traites pour terminer une lecon. Ouvrir la page ne
# suffit pas : une lecon terminee doit vouloir dire quelque chose.
INTERACTIVE_BLOCK_TYPES = frozenset(
    {BLOCK_MULTIPLE_CHOICE, BLOCK_QUESTION, BLOCK_PROMPT_EXERCISE, BLOCK_AI_EXERCISE}
)

DIFFICULTIES = frozenset({"beginner", "intermediate", "advanced"})

STATUS_NOT_STARTED = "not_started"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUSES = frozenset({STATUS_NOT_STARTED, STATUS_IN_PROGRESS, STATUS_COMPLETED})

# Un exercice IA prend un texte libre court. La borne est basse volontairement :
# Learn fait pratiquer une consigne, pas traiter un document.
MAX_EXERCISE_INPUT = 2000

# Champs obligatoires par niveau. Declares plutot qu'ecrits en `if` successifs :
# la liste se lit, et le validateur ne peut pas en oublier un.
_PATH_FIELDS = ("id", "pillar", "title", "description", "order")
_MODULE_FIELDS = ("id", "title", "description", "order")
_LESSON_FIELDS = (
    "id",
    "title",
    "description",
    "estimated_minutes",
    "difficulty",
    "order",
    "version",
)
_BLOCK_FIELDS = ("id", "type", "order", "content")


class CatalogError(Exception):
    """Catalogue invalide. Le message nomme le defaut et son emplacement."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CatalogError(message)


def _require_fields(payload: dict, fields: tuple[str, ...], where: str) -> None:
    for field in fields:
        _require(field in payload, f"{where}: champ obligatoire absent `{field}`")
        value = payload[field]
        if isinstance(value, str):
            _require(value.strip() != "", f"{where}: `{field}` vide")


def _validate_orders(items: list[dict], where: str) -> None:
    """Les `order` doivent etre uniques et former une suite depuis 1.

    Un doublon ne casserait rien visiblement : deux lecons prendraient un ordre
    arbitraire, stable sur une machine et different sur une autre. C'est le
    genre de defaut qu'on ne voit qu'en reproduction.
    """
    orders = [item.get("order") for item in items]
    for order in orders:
        _require(
            isinstance(order, int) and not isinstance(order, bool) and order >= 1,
            f"{where}: `order` doit etre un entier >= 1",
        )
    _require(len(set(orders)) == len(orders), f"{where}: `order` en double")
    _require(
        sorted(orders) == list(range(1, len(orders) + 1)),
        f"{where}: `order` doit former une suite depuis 1",
    )


def _validate_block(block: dict, where: str, exercise_ids: set[str]) -> None:
    _require_fields(block, _BLOCK_FIELDS, where)
    block_type = block["type"]
    _require(block_type in BLOCK_TYPES, f"{where}: type de bloc inconnu `{block_type}`")
    content = block["content"]
    _require(isinstance(content, dict), f"{where}: `content` doit etre un objet")

    if block_type in {BLOCK_TEXT, BLOCK_TIP, BLOCK_WARNING, BLOCK_TAKEAWAY}:
        _require(str(content.get("body", "")).strip() != "", f"{where}: `body` vide")
    elif block_type == BLOCK_EXAMPLE:
        _require(str(content.get("body", "")).strip() != "", f"{where}: `body` vide")
    elif block_type == BLOCK_COMPARISON:
        for field in ("worse", "better"):
            _require(
                str(content.get(field, "")).strip() != "", f"{where}: `{field}` vide"
            )
    elif block_type in {BLOCK_MULTIPLE_CHOICE, BLOCK_QUESTION}:
        _require(
            str(content.get("question", "")).strip() != "", f"{where}: `question` vide"
        )
        options = content.get("options")
        _require(
            isinstance(options, list) and len(options) >= 2,
            f"{where}: au moins deux options",
        )
        option_ids = []
        for option in options:
            _require(isinstance(option, dict), f"{where}: option malformee")
            _require(
                str(option.get("id", "")).strip() != ""
                and str(option.get("label", "")).strip() != "",
                f"{where}: option incomplete",
            )
            option_ids.append(option["id"])
        _require(len(set(option_ids)) == len(option_ids), f"{where}: options en double")
        correct = content.get("correct_option_id")
        _require(
            correct in option_ids,
            f"{where}: `correct_option_id` ne designe aucune option",
        )
        _require(
            str(content.get("explanation", "")).strip() != "",
            f"{where}: une reponse sans explication n'apprend rien",
        )
    elif block_type == BLOCK_REFLECTION:
        _require(str(content.get("prompt", "")).strip() != "", f"{where}: `prompt` vide")
    elif block_type == BLOCK_PROMPT_EXERCISE:
        _require(
            str(content.get("instruction", "")).strip() != "",
            f"{where}: `instruction` vide",
        )
        _require(
            str(content.get("sample_answer", "")).strip() != "",
            f"{where}: un exercice sans reponse de reference ne peut rien montrer",
        )
    elif block_type == BLOCK_AI_EXERCISE:
        _require(
            str(content.get("instruction", "")).strip() != "",
            f"{where}: `instruction` vide",
        )
        exercise_id = content.get("exercise_id")
        _require(
            isinstance(exercise_id, str) and exercise_id in exercise_ids,
            f"{where}: `exercise_id` ne designe aucun exercice declare",
        )
        # L'instruction affichee au lecteur est une consigne d'ecran. Elle ne
        # doit surtout pas etre confondue avec l'instruction envoyee au modele,
        # qui vit dans `exercises` et ne descend jamais au poste.
        _require(
            "system_prompt" not in content and "prompt" not in content,
            f"{where}: un bloc ne porte pas d'instruction systeme",
        )


def validate_catalog(catalog: Any) -> dict:
    """Verifie un catalogue de bout en bout, et le renvoie inchange.

    Appele au chargement et par les tests : un catalogue invalide fait echouer
    la suite plutot que d'atteindre un poste.
    """
    _require(isinstance(catalog, dict), "catalogue: racine attendue en objet")
    version = catalog.get("catalog_version")
    _require(
        isinstance(version, int) and not isinstance(version, bool) and version >= 1,
        "catalogue: `catalog_version` doit etre un entier >= 1",
    )
    _require(
        str(catalog.get("locale", "")).strip() != "",
        "catalogue: `locale` doit etre declaree — la localisation future en depend",
    )

    exercises = catalog.get("exercises", [])
    _require(isinstance(exercises, list), "catalogue: `exercises` doit etre une liste")
    exercise_ids: set[str] = set()
    for exercise in exercises:
        _require(isinstance(exercise, dict), "exercice: objet attendu")
        for field in ("id", "task", "instruction"):
            _require(
                str(exercise.get(field, "")).strip() != "",
                f"exercice: `{field}` vide",
            )
        _require(
            exercise["id"] not in exercise_ids,
            f"exercice: identifiant en double `{exercise['id']}`",
        )
        exercise_ids.add(exercise["id"])

    paths = catalog.get("paths")
    _require(
        isinstance(paths, list) and paths, "catalogue: `paths` doit etre une liste non vide"
    )
    _validate_orders(paths, "paths")

    seen_paths: set[str] = set()
    seen_modules: set[str] = set()
    seen_lessons: set[str] = set()
    seen_pillars: set[str] = set()
    used_exercises: set[str] = set()

    for path in paths:
        _require(isinstance(path, dict), "path: objet attendu")
        _require_fields(path, _PATH_FIELDS, "path")
        path_id = path["id"]
        _require(path_id not in seen_paths, f"path: identifiant en double `{path_id}`")
        seen_paths.add(path_id)
        _require(
            path["pillar"] in PILLARS,
            f"path `{path_id}`: pilier inconnu `{path['pillar']}`",
        )
        seen_pillars.add(path["pillar"])

        modules = path.get("modules")
        _require(
            isinstance(modules, list) and modules,
            f"path `{path_id}`: `modules` doit etre une liste non vide",
        )
        _validate_orders(modules, f"path `{path_id}`")

        for module in modules:
            _require(isinstance(module, dict), "module: objet attendu")
            _require_fields(module, _MODULE_FIELDS, f"module de `{path_id}`")
            module_id = module["id"]
            _require(
                module_id not in seen_modules,
                f"module: identifiant en double `{module_id}`",
            )
            seen_modules.add(module_id)

            lessons = module.get("lessons")
            _require(
                isinstance(lessons, list) and lessons,
                f"module `{module_id}`: `lessons` doit etre une liste non vide",
            )
            _validate_orders(lessons, f"module `{module_id}`")

            for lesson in lessons:
                _require(isinstance(lesson, dict), "lesson: objet attendu")
                where = f"lesson `{lesson.get('id', '?')}`"
                _require_fields(lesson, _LESSON_FIELDS, where)
                lesson_id = lesson["id"]
                _require(
                    lesson_id not in seen_lessons,
                    f"lesson: identifiant en double `{lesson_id}`",
                )
                seen_lessons.add(lesson_id)
                _require(
                    lesson["difficulty"] in DIFFICULTIES,
                    f"{where}: difficulte inconnue `{lesson['difficulty']}`",
                )
                minutes = lesson["estimated_minutes"]
                _require(
                    isinstance(minutes, int)
                    and not isinstance(minutes, bool)
                    and 1 <= minutes <= 10,
                    f"{where}: `estimated_minutes` hors bornes — Learn tient en 3 a 5 minutes",
                )
                lesson_version = lesson["version"]
                _require(
                    isinstance(lesson_version, int)
                    and not isinstance(lesson_version, bool)
                    and lesson_version >= 1,
                    f"{where}: `version` doit etre un entier >= 1",
                )

                blocks = lesson.get("blocks")
                _require(
                    isinstance(blocks, list) and blocks,
                    f"{where}: `blocks` doit etre une liste non vide",
                )
                _validate_orders(blocks, where)
                block_ids: set[str] = set()
                for block in blocks:
                    _require(isinstance(block, dict), f"{where}: bloc malforme")
                    block_where = f"{where} bloc `{block.get('id', '?')}`"
                    _validate_block(block, block_where, exercise_ids)
                    _require(
                        block["id"] not in block_ids,
                        f"{block_where}: identifiant de bloc en double",
                    )
                    block_ids.add(block["id"])
                    if block["type"] == BLOCK_AI_EXERCISE:
                        used_exercises.add(block["content"]["exercise_id"])

    _require(
        seen_pillars == PILLARS,
        f"catalogue: les trois piliers sont obligatoires — manquants {sorted(PILLARS - seen_pillars)}",
    )
    unused = exercise_ids - used_exercises
    _require(not unused, f"catalogue: exercices declares mais jamais references {sorted(unused)}")
    return catalog


_CACHE: dict[str, dict] = {}
_CACHE_LOCK = threading.Lock()


def catalog_language(locale: str | None) -> str:
    """La langue demandee, reduite a sa forme de base (`fr-FR` -> `fr`).

    Ce qui n'a pas la forme d'un code de langue retombe sur l'anglais : la
    valeur vient du poste et finit dans un nom de fichier.
    """
    base = str(locale or "").strip().replace("_", "-").split("-")[0].lower()
    return base if _LANGUAGE.match(base) else DEFAULT_LOCALE


def available_locales() -> list[str]:
    """Les traductions livrees a cote du catalogue anglais."""
    found = []
    for candidate in CATALOG_DIR.glob("catalog.*.json"):
        language = candidate.name.split(".")[1]
        if _LANGUAGE.match(language) and language != DEFAULT_LOCALE:
            found.append(language)
    return sorted(found)


def load_catalog(path: Path | None = None, locale: str | None = None) -> dict:
    """Catalogue valide, lu une fois par langue puis garde en memoire.

    Le fichier est livre avec le serveur et ne change pas en cours
    d'execution ; le relire a chaque requete n'aurait servi qu'a multiplier les
    occasions d'echouer. Une langue sans traduction recoit le catalogue
    anglais plutot qu'une erreur : Learn reste lisible, et le poste signale la
    langue du contenu.
    """
    if path is not None:
        with path.open(encoding="utf-8") as handle:
            return validate_catalog(json.load(handle))
    language = catalog_language(locale)
    source = CATALOG_DIR / f"catalog.{language}.json"
    if language == DEFAULT_LOCALE or not source.is_file():
        language, source = DEFAULT_LOCALE, CATALOG_PATH
    with _CACHE_LOCK:
        if language not in _CACHE:
            with source.open(encoding="utf-8") as handle:
                catalog = validate_catalog(json.load(handle))
            _require(
                catalog["locale"] == language,
                f"catalogue `{source.name}`: `locale` devrait valoir `{language}`",
            )
            _CACHE[language] = catalog
        return _CACHE[language]


def reset_cache() -> None:
    """Vide le cache. Reserve aux tests."""
    with _CACHE_LOCK:
        _CACHE.clear()


def public_catalog(catalog: dict | None = None, locale: str | None = None) -> dict:
    """Le catalogue tel qu'un poste a le droit de le voir.

    `exercises` en est retire : ce bloc ne contient que des instructions
    destinees au modele. Les publier reviendrait a laisser le poste les
    reecrire, ce que `/api/learning/exercises/{id}/feedback` refuse justement
    de faire.
    """
    source = catalog if catalog is not None else load_catalog(locale=locale)
    return {key: value for key, value in source.items() if key != "exercises"}


def lessons_index(catalog: dict | None = None) -> dict[str, dict]:
    """Toutes les lecons par identifiant, avec le chemin qui les porte."""
    source = catalog if catalog is not None else load_catalog()
    index: dict[str, dict] = {}
    for path in source["paths"]:
        for module in path["modules"]:
            for lesson in module["lessons"]:
                index[lesson["id"]] = {
                    "lesson": lesson,
                    "module_id": module["id"],
                    "path_id": path["id"],
                    "pillar": path["pillar"],
                }
    return index


def exercise(exercise_id: str, catalog: dict | None = None) -> dict | None:
    source = catalog if catalog is not None else load_catalog()
    for item in source.get("exercises", []):
        if item["id"] == exercise_id:
            return item
    return None


def required_block_ids(lesson: dict) -> list[str]:
    """Les blocs qu'il faut avoir traites pour terminer la lecon.

    Une lecon sans interaction n'en a aucun : elle se termine en atteignant le
    dernier bloc. Une lecon qui pose une question exige qu'on y ait repondu —
    sinon « termine » ne voudrait dire que « la page a ete ouverte ».
    """
    return [
        block["id"]
        for block in sorted(lesson["blocks"], key=lambda item: item["order"])
        if block["type"] in INTERACTIVE_BLOCK_TYPES
    ]


def completion_is_earned(lesson: dict, completed_block_ids: list[str]) -> bool:
    """La lecon peut-elle passer a `completed` ?

    Le dernier bloc doit avoir ete atteint, et toute interaction obligatoire
    realisee. C'est verifie ici, cote serveur : un poste qui enverrait
    `completed` directement ne doit pas suffire a l'obtenir.
    """
    blocks = sorted(lesson["blocks"], key=lambda item: item["order"])
    seen = set(completed_block_ids)
    if blocks[-1]["id"] not in seen:
        return False
    return all(block_id in seen for block_id in required_block_ids(lesson))
