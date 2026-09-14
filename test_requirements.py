"""Dependances du serveur.

Sans version figee, une publication d'une librairie suffisait a faire echouer
la CI — ou a changer le comportement d'une image reconstruite — sans qu'une
ligne de Nova ait bouge. L'image de production et la suite de tests doivent
installer exactement ce qui a ete teste.
"""

import re
import unittest
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parent / "requirements.txt"
PINNED = re.compile(r"^[A-Za-z0-9_.-]+(\[[A-Za-z0-9_,.-]+\])?==[0-9][0-9A-Za-z.+-]*$")


def requirement_lines() -> list[str]:
    lines = []
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            lines.append(line)
    return lines


class RequirementsTests(unittest.TestCase):
    def test_there_are_requirements(self):
        self.assertGreaterEqual(len(requirement_lines()), 6)

    def test_every_dependency_has_an_exact_version(self):
        loose = [line for line in requirement_lines() if not PINNED.match(line)]
        self.assertEqual(loose, [])


if __name__ == "__main__":
    unittest.main()
