"""Le catalogue Learn par defaut — un seul catalogue, quatre parcours.

Nova proposait deux cours sans rapport : « Fondamentaux IA », six modules
integres a l'application, et les neuf lecons de Learn servies par le serveur,
rangees en trois piliers (Use AI, Learn AI, Adapt AI). Deux endroits, deux
formats, et plusieurs sujets traites deux fois.

Il n'y a plus qu'un catalogue, organise en quatre parcours thematiques. Les
modules de « Fondamentaux IA » y deviennent des lecons, sans doublon, et les
identifiants des neuf lecons existantes ne changent pas : la progression deja
enregistree reste valable.
"""

import unittest

from test_main import AdminTestCase  # noqa: F401

import learning

EXISTING_LESSONS = {
    "ask-better-questions",
    "give-useful-context",
    "iterate-instead-of-restarting",
    "what-an-llm-does",
    "why-ai-hallucinates",
    "context-and-tokens",
    "an-assistant-for-your-work",
    "build-a-reusable-workflow",
    "adapt-ai-to-your-domain",
}

AI_ESSENTIALS_LESSONS = {
    "working-with-ai",
    "verify-output",
    "confidential-information",
    "responsible-use",
    "ai-in-engineering",
}

EXPECTED_PATHS = [
    ("getting-started", ["working-with-ai", "what-an-llm-does", "context-and-tokens"]),
    ("asking-well", ["ask-better-questions", "give-useful-context",
                     "iterate-instead-of-restarting"]),
    ("verify-and-protect", ["why-ai-hallucinates", "verify-output",
                            "confidential-information", "responsible-use"]),
    ("adapt-to-your-field", ["an-assistant-for-your-work", "build-a-reusable-workflow",
                             "adapt-ai-to-your-domain", "ai-in-engineering"]),
]


def ordered_paths(catalog):
    result = []
    for path in sorted(catalog["paths"], key=lambda item: item["order"]):
        lessons = [
            lesson["id"]
            for module in sorted(path["modules"], key=lambda item: item["order"])
            for lesson in sorted(module["lessons"], key=lambda item: item["order"])
        ]
        result.append((path["id"], lessons))
    return result


class DefaultCatalogueTests(unittest.TestCase):
    def setUp(self):
        learning.reset_cache()

    def test_four_thematic_paths_in_reading_order(self):
        self.assertEqual(ordered_paths(learning.load_catalog()), EXPECTED_PATHS)

    def test_the_catalogue_version_moves_on(self):
        self.assertEqual(learning.load_catalog()["catalog_version"], 2)

    def test_existing_lessons_keep_their_identity_and_version(self):
        """Une lecon deja commencee ne doit pas repartir de zero."""
        index = learning.lessons_index()
        for lesson_id in EXISTING_LESSONS:
            with self.subTest(lesson=lesson_id):
                self.assertIn(lesson_id, index)
                self.assertEqual(index[lesson_id]["lesson"]["version"], 1)

    def test_ai_essentials_modules_are_lessons_now(self):
        index = learning.lessons_index()
        for lesson_id in AI_ESSENTIALS_LESSONS:
            with self.subTest(lesson=lesson_id):
                lesson = index[lesson_id]["lesson"]
                types = [block["type"] for block in
                         sorted(lesson["blocks"], key=lambda item: item["order"])]
                self.assertIn("multiple_choice", types)
                self.assertEqual(types[-1], "takeaway")

    def test_no_topic_is_taught_twice(self):
        """« Ask better » repetait « Ask better questions » : il n'est pas repris."""
        self.assertNotIn("ask-better", learning.lessons_index())
        self.assertEqual(
            set(learning.lessons_index()), EXISTING_LESSONS | AI_ESSENTIALS_LESSONS
        )

    def test_every_translation_follows_the_same_four_paths(self):
        for locale in learning.available_locales():
            with self.subTest(locale=locale):
                self.assertEqual(
                    ordered_paths(learning.load_catalog(locale=locale)), EXPECTED_PATHS
                )

    def test_the_french_path_titles_say_what_the_path_is_for(self):
        titles = [path["title"] for path in
                  sorted(learning.load_catalog(locale="fr")["paths"],
                         key=lambda item: item["order"])]
        self.assertEqual(
            titles,
            ["Bien démarrer", "Bien demander", "Vérifier et protéger",
             "Adapter à son domaine"],
        )


if __name__ == "__main__":
    unittest.main()
