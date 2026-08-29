"""La couche d'accès, vérifiée contre les deux moteurs réels.

Les tests PostgreSQL ne sont pas simulés : ils s'exécutent contre un vrai
serveur si `NOVA_TEST_POSTGRES_URL` est défini, et sont ignorés sinon. Un test
d'adaptateur qui ne parlerait qu'à SQLite ne prouverait rien de ce que cette
couche existe pour garantir.
"""

import os
import tempfile
import threading
import unittest
import uuid

import database


POSTGRES_URL = os.getenv("NOVA_TEST_POSTGRES_URL", "").strip()


class UrlParsingTests(unittest.TestCase):
    def test_every_url_form_that_actually_circulates_is_understood(self):
        for url, dialect in (
            ("sqlite:///C:/tmp/nova.db", database.SQLITE),
            ("sqlite:///./nova.db", database.SQLITE),
            ("sqlite:nova.db", database.SQLITE),
            ("postgresql://u:p@host:5432/db", database.POSTGRESQL),
            ("postgres://u:p@host/db", database.POSTGRESQL),
        ):
            self.assertEqual(database.parse_url(url).dialect, dialect, url)

    def test_postgres_scheme_is_normalised_so_logs_show_one_engine(self):
        self.assertTrue(
            database.parse_url("postgres://u@h/db").url.startswith("postgresql://")
        )

    def test_an_unknown_or_empty_scheme_is_refused_rather_than_guessed(self):
        for url in ("", "   ", "mysql://h/db", "redis://h", "nova.db"):
            with self.assertRaises(database.DatabaseError, msg=url):
                database.parse_url(url)

    def test_the_legacy_nova_db_variable_still_resolves(self):
        # Les recettes et les tests existants passent NOVA_DB. Les casser
        # aurait transformé un durcissement en migration forcée.
        previous = os.environ.get("NOVA_DB")
        os.environ.pop("NOVA_DATABASE_URL", None)
        os.environ["NOVA_DB"] = r"C:\tmp\legacy.db"
        try:
            config = database.parse_url(database.configured_url())
            self.assertEqual(config.dialect, database.SQLITE)
            self.assertIn("legacy.db", config.path)
        finally:
            os.environ.pop("NOVA_DB", None)
            if previous is not None:
                os.environ["NOVA_DB"] = previous

    def test_an_explicit_url_wins_over_the_legacy_variable(self):
        previous_url = os.environ.get("NOVA_DATABASE_URL")
        previous_db = os.environ.get("NOVA_DB")
        os.environ["NOVA_DB"] = "ignored.db"
        os.environ["NOVA_DATABASE_URL"] = "postgresql://u@h/db"
        try:
            self.assertEqual(
                database.parse_url(database.configured_url()).dialect,
                database.POSTGRESQL,
            )
        finally:
            for key, value in (("NOVA_DB", previous_db), ("NOVA_DATABASE_URL", previous_url)):
                os.environ.pop(key, None)
                if value is not None:
                    os.environ[key] = value


class PlaceholderTranslationTests(unittest.TestCase):
    def test_parameters_become_postgres_placeholders(self):
        self.assertEqual(
            database.translate_placeholders("SELECT * FROM t WHERE a=? AND b=?"),
            "SELECT * FROM t WHERE a=%s AND b=%s",
        )

    def test_a_question_mark_inside_a_literal_is_left_alone(self):
        # C'est le bug que cette fonction existe pour ne pas commettre : une
        # traduction naïve corromprait le contenu d'un message stocké.
        for sql, expected in (
            ("INSERT INTO t(m) VALUES ('why?')", "INSERT INTO t(m) VALUES ('why?')"),
            (
                "SELECT ? FROM t WHERE m='a?b'",
                "SELECT %s FROM t WHERE m='a?b'",
            ),
            ("SELECT '?' , ?", "SELECT '?' , %s"),
        ):
            self.assertEqual(database.translate_placeholders(sql), expected, sql)

    def test_every_percent_is_doubled_including_inside_literals(self):
        # psycopg analyse les `%` sur la requête entière, littéraux compris :
        # un `LIKE '%match'` non doublé est lu comme le marqueur `%m` et
        # rejeté. Il les ramène à un seul en interpolant.
        self.assertEqual(
            database.translate_placeholders("SELECT * FROM t WHERE a LIKE '%x%' AND b=?"),
            "SELECT * FROM t WHERE a LIKE '%%x%%' AND b=%s",
        )

    def test_a_question_mark_inside_a_literal_stays_a_question_mark(self):
        self.assertEqual(
            database.translate_placeholders("SELECT '100%?' , ?"),
            "SELECT '100%%?' , %s",
        )

    def test_comments_are_not_rewritten(self):
        self.assertEqual(
            database.translate_placeholders("SELECT 1 -- is it ? yes\n, ?"),
            "SELECT 1 -- is it ? yes\n, %s",
        )

    def test_sqlite_only_constructs_are_refused_instead_of_forwarded(self):
        # Les laisser passer serait pire que de les refuser : PostgreSQL les
        # rejetterait à l'exécution, ou pis, les comprendrait autrement.
        for sql in (
            "INSERT OR REPLACE INTO t(a) VALUES (?)",
            "CREATE TABLE t(id INTEGER PRIMARY KEY AUTOINCREMENT)",
            "PRAGMA table_info(t)",
            "BEGIN IMMEDIATE",
        ):
            with self.assertRaises(database.DatabaseError, msg=sql):
                database.translate_sql(sql, database.POSTGRESQL)

    def test_insert_or_ignore_gets_its_exact_equivalent(self):
        translated = database.translate_sql(
            "INSERT OR IGNORE INTO t(a,b) VALUES (?,?)", database.POSTGRESQL
        )
        self.assertIn("ON CONFLICT DO NOTHING", translated)
        self.assertNotIn("OR IGNORE", translated)
        self.assertIn("VALUES (%s,%s)", translated)

    def test_sqlite_receives_its_own_sql_untouched(self):
        sql = "INSERT OR REPLACE INTO t(a) VALUES (?)"
        self.assertEqual(database.translate_sql(sql, database.SQLITE), sql)


class RowTests(unittest.TestCase):
    def test_a_row_indexes_by_name_and_by_position_like_sqlite(self):
        row = database.Row(["email", "count"], ["a@example.com", 3])
        self.assertEqual(row["email"], "a@example.com")
        self.assertEqual(row[0], "a@example.com")
        self.assertEqual(row[1], 3)
        self.assertEqual(row.keys(), ["email", "count"])
        self.assertEqual(dict(zip(row.keys(), row)), {"email": "a@example.com", "count": 3})
        self.assertIn("email", row)
        self.assertNotIn("absent", row)
        self.assertEqual(len(row), 2)


class EngineContractTests:
    """Contrat identique sous les deux moteurs. Sous-classé, jamais exécuté seul."""

    url: str

    def setUp(self):
        self.db = database.Database(self.url)
        self.table = "contract_" + uuid.uuid4().hex[:8]
        with self.db.connect() as conn:
            conn.executescript(
                f"""
                CREATE TABLE {self.table}(
                    organization_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    label TEXT,
                    PRIMARY KEY (organization_id, key)
                );
                """
            )

    def tearDown(self):
        with self.db.connect() as conn:
            conn.executescript(f"DROP TABLE IF EXISTS {self.table}")
        self.db.close()

    def test_insert_and_read_back_by_name_and_position(self):
        with self.db.connect() as conn:
            conn.execute(
                f"INSERT INTO {self.table}(organization_id, key, label) VALUES (?,?,?)",
                ("org-a", "k1", "Label"),
            )
        with self.db.connect() as conn:
            row = conn.execute(
                f"SELECT organization_id, key, label FROM {self.table} WHERE key=?", ("k1",)
            ).fetchone()
        self.assertEqual(row["organization_id"], "org-a")
        self.assertEqual(row[1], "k1")
        self.assertEqual(row["label"], "Label")

    def test_a_unique_violation_raises_the_same_exception_on_both_engines(self):
        # C'est la garantie centrale : un conflit doit rester un 409 après le
        # passage en production, pas devenir un 500.
        with self.db.connect() as conn:
            conn.execute(
                f"INSERT INTO {self.table}(organization_id, key) VALUES (?,?)", ("org-a", "dup")
            )
        with self.assertRaises(database.IntegrityError):
            with self.db.connect() as conn:
                conn.execute(
                    f"INSERT INTO {self.table}(organization_id, key) VALUES (?,?)",
                    ("org-a", "dup"),
                )

    def test_a_failed_transaction_leaves_nothing_behind(self):
        with self.assertRaises(database.IntegrityError):
            with self.db.connect() as conn:
                conn.execute(
                    f"INSERT INTO {self.table}(organization_id, key) VALUES (?,?)",
                    ("org-a", "rollback-me"),
                )
                conn.execute(
                    f"INSERT INTO {self.table}(organization_id, key) VALUES (?,?)",
                    ("org-a", "rollback-me"),
                )
        with self.db.connect() as conn:
            remaining = conn.execute(
                f"SELECT COUNT(*) FROM {self.table} WHERE key=?", ("rollback-me",)
            ).fetchone()[0]
        self.assertEqual(remaining, 0)

    def test_insert_or_ignore_is_idempotent_on_both_engines(self):
        with self.db.connect() as conn:
            for _ in range(3):
                conn.execute(
                    f"INSERT OR IGNORE INTO {self.table}(organization_id, key) VALUES (?,?)",
                    ("org-a", "once"),
                )
        with self.db.connect() as conn:
            count = conn.execute(
                f"SELECT COUNT(*) FROM {self.table} WHERE key=?", ("once",)
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_total_changes_counts_the_same_way_on_both_engines(self):
        with self.db.connect() as conn:
            before = conn.total_changes
            conn.executemany(
                f"INSERT OR IGNORE INTO {self.table}(organization_id, key) VALUES (?,?)",
                [("org-a", "m1"), ("org-a", "m2"), ("org-a", "m3")],
            )
            self.assertEqual(conn.total_changes - before, 3)

    def test_column_introspection_needs_no_pragma(self):
        with self.db.connect() as conn:
            columns = conn.columns(self.table)
        self.assertEqual(sorted(columns), ["key", "label", "organization_id"])

    def test_table_existence_is_answerable_on_both_engines(self):
        with self.db.connect() as conn:
            self.assertTrue(conn.table_exists(self.table))
            self.assertFalse(conn.table_exists("table_that_does_not_exist"))

    def test_an_unsafe_table_identifier_is_refused(self):
        with self.db.connect() as conn:
            for name in ("t; DROP TABLE x", "t x", "", "1t"):
                with self.assertRaises(database.DatabaseError, msg=name):
                    conn.columns(name)

    def test_a_literal_question_mark_survives_a_round_trip(self):
        with self.db.connect() as conn:
            conn.execute(
                f"INSERT INTO {self.table}(organization_id, key, label) VALUES (?,?,'why?')",
                ("org-a", "literal"),
            )
        with self.db.connect() as conn:
            row = conn.execute(
                f"SELECT label FROM {self.table} WHERE key=?", ("literal",)
            ).fetchone()
        self.assertEqual(row["label"], "why?")

    def test_a_like_pattern_survives_a_round_trip(self):
        with self.db.connect() as conn:
            conn.execute(
                f"INSERT INTO {self.table}(organization_id, key, label) VALUES (?,?,?)",
                ("org-a", "like", "prefix-match"),
            )
        with self.db.connect() as conn:
            row = conn.execute(
                f"SELECT label FROM {self.table} WHERE label LIKE '%match' AND key=?",
                ("like",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["label"], "prefix-match")

    def test_concurrent_inserts_produce_exactly_one_winner(self):
        # « Vérifier puis insérer » ne suffit pas : c'est la contrainte qui
        # tranche, et elle doit trancher pareil sous les deux moteurs.
        errors: list[Exception] = []
        successes: list[int] = []
        barrier = threading.Barrier(4)

        def attempt():
            barrier.wait()
            try:
                with self.db.connect() as conn:
                    conn.begin_immediate()
                    conn.execute(
                        f"INSERT INTO {self.table}(organization_id, key) VALUES (?,?)",
                        ("org-a", "race"),
                    )
                successes.append(1)
            except database.IntegrityError:
                pass
            except Exception as error:  # pragma: no cover - diagnostic
                errors.append(error)

        threads = [threading.Thread(target=attempt) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(successes), 1)
        with self.db.connect() as conn:
            count = conn.execute(
                f"SELECT COUNT(*) FROM {self.table} WHERE key=?", ("race",)
            ).fetchone()[0]
        self.assertEqual(count, 1)


class SqliteContractTests(EngineContractTests, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._directory = tempfile.TemporaryDirectory()
        path = os.path.join(cls._directory.name, "contract.db").replace("\\", "/")
        cls.url = "sqlite:///" + path

    @classmethod
    def tearDownClass(cls):
        cls._directory.cleanup()


@unittest.skipUnless(POSTGRES_URL, "NOVA_TEST_POSTGRES_URL is not set")
class PostgresContractTests(EngineContractTests, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = POSTGRES_URL


@unittest.skipUnless(POSTGRES_URL, "NOVA_TEST_POSTGRES_URL is not set")
class PostgresPoolTests(unittest.TestCase):
    def test_the_pool_is_bounded_and_refuses_rather_than_growing(self):
        previous = os.environ.get("NOVA_DB_POOL_MAX")
        os.environ["NOVA_DB_POOL_MAX"] = "2"
        os.environ["NOVA_DB_POOL_TIMEOUT"] = "1"
        try:
            db = database.Database(POSTGRES_URL)
            held = [db.connect(), db.connect()]
            for context in held:
                context.__enter__()
            try:
                self.assertEqual(db.pool_stats["leased"], 2)
                with self.assertRaises(database.OperationalError):
                    with db.connect():
                        pass
            finally:
                for context in held:
                    context.__exit__(None, None, None)
            # Les connexions rendues redeviennent disponibles.
            self.assertEqual(db.pool_stats["leased"], 0)
            with db.connect() as conn:
                self.assertEqual(conn.execute("SELECT 1").fetchone()[0], 1)
            db.close()
        finally:
            os.environ.pop("NOVA_DB_POOL_MAX", None)
            os.environ.pop("NOVA_DB_POOL_TIMEOUT", None)
            if previous is not None:
                os.environ["NOVA_DB_POOL_MAX"] = previous

    def test_an_unreachable_database_fails_instead_of_falling_back(self):
        db_url = "postgresql://postgres:wrong@127.0.0.1:1/nonexistent"
        with self.assertRaises((database.OperationalError, database.DatabaseError)):
            database.Database(db_url)


if __name__ == "__main__":
    unittest.main()
