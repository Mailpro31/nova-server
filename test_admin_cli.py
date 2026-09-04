"""Phase 28 — la ligne de commande d'opérateur.

Ces tests vivent à part parce qu'ils exercent un **chemin local**, pas le
serveur HTTP. `admin_cli.py discovery` est devenu critique en Phase 28 : le
jeton partagé qui servait de sortie de secours à l'amorçage a disparu, et cette
commande a pris sa place. Un chemin d'amorçage sans test est un chemin dont on
découvre les défauts le jour où l'on en a le plus besoin.

Les tests appellent `admin_cli.run()` plutôt que les fonctions directement :
c'est ainsi que l'opérateur s'en sert, et l'analyse des arguments fait partie de
ce qu'il faut couvrir. Aucun sous-processus — il rendrait les échecs plus
difficiles à lire sans rien prouver de plus.
"""

import contextlib
import importlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEST_DIRECTORY = tempfile.TemporaryDirectory()
# Exactement les mêmes valeurs que `test_main.py` : les trois fichiers importent
# le même module, et le premier importé fixe la configuration pour tous.
os.environ.setdefault("NOVA_DB", os.path.join(TEST_DIRECTORY.name, "admin-cli.db"))
os.environ.setdefault("NOVA_DOMAINS", "example.com")
os.environ.setdefault("NOVA_ORGANIZATION_NAME", "Nova Test School")
os.environ.setdefault("NOVA_ORGANIZATION_SHORT_NAME", "NTS")
os.environ.setdefault("NOVA_ENTRA_CLIENT_ID", "")

import admin_cli  # noqa: E402
import main  # noqa: E402

# Le schema s'applique **explicitement**. Importer `main` ne migre plus rien :
# c'est precisement l'effet de bord qui a migre une base de production en
# Phase 30 puis en Phase 37. Un appel visible dans le test remplace un import
# qui decidait tout seul.
main.init_db()


def run(*argv) -> tuple[int, str]:
    """Lance la CLI et capture ce qu'elle affiche.

    Renvoie le code de sortie **et** la sortie : un code correct accompagné d'un
    message incompréhensible reste un défaut pour un outil d'exploitation.
    """
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        code = admin_cli.run(list(argv))
    return code, stream.getvalue()


def organization_row() -> dict:
    with main.db() as conn:
        row = conn.execute(
            "SELECT * FROM organizations WHERE id=?", (main.nova_organization_id(),)
        ).fetchone()
    return dict(row)


def reset_discovery(enabled: int = 0, endpoint=None, slug: str = "ecole-test"):
    with main.db() as conn:
        conn.execute(
            "UPDATE organizations SET discovery_slug=?, discovery_enabled=?, "
            "service_endpoint=?, status='active' WHERE id=?",
            (slug, enabled, endpoint, main.nova_organization_id()),
        )


class DatabaseIsolationTests(unittest.TestCase):
    """La base du développeur ne doit jamais être ouverte par les tests.

    Ces tests écrivent réellement dans `organizations`. S'ils visaient la base
    locale, ils désactiveraient la découverte d'une installation en cours de
    travail — et le feraient silencieusement.
    """

    def test_the_database_lives_in_a_temporary_directory(self):
        path = Path(main.DB_PATH).resolve()
        self.assertNotEqual(path, Path("nova.db").resolve())
        self.assertTrue(
            str(path).startswith(str(Path(tempfile.gettempdir()).resolve())),
            f"la base des tests devrait être temporaire : {path}",
        )

    def test_the_local_database_is_never_the_one_under_test(self):
        """`nova.db`, à côté des sources, est la base d'une installation réelle.

        Elle existe le plus souvent sur la machine d'un développeur, et elle
        n'est pas versionnée. Le test n'exige donc pas son absence — il exige
        qu'elle ne soit pas celle que les tests ouvrent.
        """
        local = Path(__file__).with_name("nova.db")
        self.assertNotEqual(Path(main.DB_PATH).resolve(), local.resolve())

    def test_a_mutation_leaves_the_local_database_untouched(self):
        local = Path(__file__).with_name("nova.db")
        before = local.stat().st_mtime_ns if local.exists() else None
        run("discovery", "enable", "--endpoint", "https://control.example.com")
        run("discovery", "disable")
        after = local.stat().st_mtime_ns if local.exists() else None
        self.assertEqual(before, after, "les tests ont écrit dans la base locale")


class DiscoveryShowTests(unittest.TestCase):
    def setUp(self):
        reset_discovery()

    def test_show_reports_a_closed_organization(self):
        code, output = run("discovery", "show")
        self.assertEqual(code, 0)
        self.assertIn("ecole-test", output)
        self.assertIn("False", output)
        self.assertIn("(aucune)", output)
        self.assertIn("active", output)

    def test_show_reports_an_open_organization(self):
        reset_discovery(enabled=1, endpoint="https://nova.example.edu")
        code, output = run("discovery", "show")
        self.assertEqual(code, 0)
        self.assertIn("True", output)
        self.assertIn("https://nova.example.edu", output)

    def test_show_changes_nothing(self):
        before = organization_row()
        run("discovery", "show")
        self.assertEqual(organization_row(), before)

    def test_show_reveals_no_secret(self):
        reset_discovery(enabled=1, endpoint="https://nova.example.edu")
        _, output = run("discovery", "show")
        for forbidden in ("token", "secret", "bearer", "client_id", "password"):
            self.assertNotIn(forbidden, output.lower())


class DiscoveryEnableTests(unittest.TestCase):
    def setUp(self):
        reset_discovery()

    def test_enable_stores_the_endpoint_and_opens_discovery(self):
        code, output = run(
            "discovery", "enable", "--endpoint", "https://control.example.com"
        )
        self.assertEqual(code, 0)
        self.assertIn("https://control.example.com", output)
        row = organization_row()
        self.assertEqual(row["discovery_enabled"], 1)
        self.assertEqual(row["service_endpoint"], "https://control.example.com")

    def test_enable_normalizes_the_stored_address(self):
        """Même normalisation que l'API : espaces et barre finale retirés.

        Deux écritures de la même adresse ne doivent pas produire deux valeurs
        différentes en base — le poste les comparerait un jour.
        """
        code, _ = run(
            "discovery", "enable", "--endpoint", "  https://control.example.com/  "
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            organization_row()["service_endpoint"], "https://control.example.com"
        )

    def test_enable_replaces_a_previous_address(self):
        run("discovery", "enable", "--endpoint", "https://ancien.example.com")
        code, _ = run("discovery", "enable", "--endpoint", "https://nouveau.example.com")
        self.assertEqual(code, 0)
        self.assertEqual(
            organization_row()["service_endpoint"], "https://nouveau.example.com"
        )

    def test_enable_without_endpoint_reuses_the_stored_one(self):
        """Contrat : réactiver ne demande pas de ressaisir l'adresse.

        C'est le pendant de `disable`, qui la conserve. Exiger `--endpoint` pour
        rouvrir ce qu'on vient de fermer inviterait à la retaper — donc à se
        tromper.
        """
        reset_discovery(enabled=0, endpoint="https://control.example.com")
        code, _ = run("discovery", "enable")
        self.assertEqual(code, 0)
        row = organization_row()
        self.assertEqual(row["discovery_enabled"], 1)
        self.assertEqual(row["service_endpoint"], "https://control.example.com")

    def test_enable_without_any_address_refuses(self):
        code, output = run("discovery", "enable")
        self.assertEqual(code, 1)
        self.assertIn("--endpoint", output)
        self.assertEqual(organization_row()["discovery_enabled"], 0)


class DiscoveryDisableTests(unittest.TestCase):
    def setUp(self):
        reset_discovery(enabled=1, endpoint="https://control.example.com")

    def test_disable_closes_discovery(self):
        code, output = run("discovery", "disable")
        self.assertEqual(code, 0)
        self.assertIn("desactivee", output)
        self.assertEqual(organization_row()["discovery_enabled"], 0)

    def test_disable_keeps_the_address(self):
        """Contrat explicite : fermer n'efface pas la configuration.

        Le même choix que l'API, qui n'écrit `service_endpoint` que si on le lui
        passe. Effacer l'adresse ferait d'une fermeture temporaire une
        reconfiguration complète.
        """
        run("discovery", "disable")
        self.assertEqual(
            organization_row()["service_endpoint"], "https://control.example.com"
        )

    def test_disable_is_idempotent(self):
        run("discovery", "disable")
        code, _ = run("discovery", "disable")
        self.assertEqual(code, 0)
        self.assertEqual(organization_row()["discovery_enabled"], 0)


class DiscoveryEndpointValidationTests(unittest.TestCase):
    """La CLI n'est pas une porte dérobée vers une adresse que l'API refuserait.

    Les cas de la politique SSRF elle-même sont couverts là où vit le helper.
    Ce qui se joue ici est différent : prouver que cette commande **passe par**
    ce helper, avec le drapeau du serveur, et qu'aucun refus ne laisse la base
    à moitié écrite.
    """

    REJECTED = {
        "http_sans_tls": "http://control.example.com",
        "schema_inconnu": "ftp://control.example.com",
        "localhost": "https://localhost",
        "sous_domaine_localhost": "https://nova.localhost",
        "boucle_locale_v4": "https://127.0.0.1",
        "boucle_locale_v6": "https://[::1]",
        "adresse_privee": "https://10.0.0.5",
        "lien_local": "https://169.254.169.254",
        "multicast": "https://224.0.0.1",
        "reservee": "https://240.0.0.1",
        "non_specifiee": "https://0.0.0.0",
        "identifiants_dans_l_url": "https://user:pass@control.example.com",
        "pas_une_url_de_base": "https://control.example.com/?a=1",
        "fragment": "https://control.example.com/#x",
        "hote_absent": "https://",
        "texte_libre": "pas-une-url",
    }

    def setUp(self):
        reset_discovery()

    def test_every_forbidden_address_is_refused(self):
        for name, endpoint in self.REJECTED.items():
            with self.subTest(name):
                reset_discovery()
                code, output = run("discovery", "enable", "--endpoint", endpoint)
                self.assertEqual(code, 1, f"{name} aurait dû être refusé")
                self.assertIn("refus", output)
                self.assertEqual(organization_row()["discovery_enabled"], 0)

    def test_a_refusal_leaves_the_database_untouched(self):
        before = organization_row()
        run("discovery", "enable", "--endpoint", "https://127.0.0.1")
        self.assertEqual(organization_row(), before)

    def test_the_command_delegates_to_the_server_helper(self):
        """Preuve directe : c'est bien `service_endpoint_is_allowed` qui décide.

        Sans ce test, une validation parallèle pourrait dériver de celle de
        l'API sans qu'aucun autre test ne s'en aperçoive.
        """
        seen = []

        def refuse(endpoint, allow_insecure):
            seen.append((endpoint, allow_insecure))
            return False, "motif-sentinelle"

        with patch.object(main, "service_endpoint_is_allowed", refuse):
            code, output = run(
                "discovery", "enable", "--endpoint", "https://control.example.com"
            )
        self.assertEqual(code, 1)
        self.assertIn("motif-sentinelle", output)
        self.assertEqual(
            seen,
            [("https://control.example.com", main.DISCOVERY_ALLOW_INSECURE_ENDPOINT)],
        )
        self.assertEqual(organization_row()["discovery_enabled"], 0)


class DiscoveryInsecureModeTests(unittest.TestCase):
    """Le mode de développement, tel qu'il est réellement implémenté.

    Il n'y a pas de `NOVA_ENV` ici : la seule bascule est
    `NOVA_DISCOVERY_ALLOW_INSECURE_ENDPOINT`, un paramètre **du serveur**. Elle
    ouvre `http://` et lève le contrôle de routabilité — donc `127.0.0.1`.
    """

    def setUp(self):
        reset_discovery()

    def test_a_loopback_address_is_refused_by_default(self):
        code, _ = run("discovery", "enable", "--endpoint", "http://127.0.0.1:8787")
        self.assertEqual(code, 1)
        self.assertEqual(organization_row()["discovery_enabled"], 0)

    def test_a_loopback_address_is_accepted_when_the_server_allows_it(self):
        with patch.object(main, "DISCOVERY_ALLOW_INSECURE_ENDPOINT", True):
            code, _ = run("discovery", "enable", "--endpoint", "http://127.0.0.1:8787")
        self.assertEqual(code, 0)
        row = organization_row()
        self.assertEqual(row["discovery_enabled"], 1)
        self.assertEqual(row["service_endpoint"], "http://127.0.0.1:8787")

    def test_the_flag_is_read_from_the_server_not_from_the_command_line(self):
        """Aucune option ne permet de relâcher la politique depuis la CLI."""
        source = Path(admin_cli.__file__).read_text(encoding="utf-8")
        self.assertNotIn("--allow-insecure", source)
        self.assertNotIn("--insecure", source)
        self.assertNotIn("--force", source)


class DiscoveryInputTests(unittest.TestCase):
    """Une erreur d'opérateur doit produire un message, jamais une trace."""

    def setUp(self):
        reset_discovery()

    def test_an_unknown_action_is_refused(self):
        code, output = run("discovery", "activate")
        self.assertEqual(code, 2)
        self.assertIn("refus", output)

    def test_discovery_without_an_action_shows_the_usage(self):
        code, output = run("discovery")
        self.assertEqual(code, 2)
        self.assertIn("admin_cli.py", output)

    def test_too_many_arguments_show_the_usage(self):
        code, output = run("discovery", "show", "encore")
        self.assertEqual(code, 2)
        self.assertIn("admin_cli.py", output)

    def test_an_endpoint_option_without_a_value_is_refused(self):
        code, output = run("discovery", "enable", "--endpoint")
        self.assertEqual(code, 2)
        self.assertIn("refus", output)

    def test_an_unknown_organization_is_refused(self):
        code, output = run(
            "discovery", "show", "--organization", "organisation-inexistante"
        )
        self.assertEqual(code, 1)
        self.assertIn("refus", output)

    def test_no_invalid_input_raises(self):
        for argv in (
            [],
            ["discovery"],
            ["discovery", "enable", "--endpoint"],
            ["discovery", "n-importe-quoi"],
            ["discovery", "enable", "--endpoint", "pas-une-url"],
            ["grant"],
            ["parfaitement-inconnu"],
        ):
            with self.subTest(argv=argv):
                try:
                    code, _ = run(*argv)
                except Exception as error:  # pragma: no cover - c'est le défaut
                    self.fail(f"{argv} a levé {error!r} au lieu de refuser proprement")
                self.assertIn(code, (1, 2))


class OperatorPathBoundaryTests(unittest.TestCase):
    """La CLI reste un chemin local. Elle n'ouvre aucun accès distant."""

    def test_it_creates_no_session_and_no_token(self):
        source = Path(admin_cli.__file__).read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        for forbidden in (
            "create_admin_session",
            "admin_sessions",
            "token_urlsafe",
            "X-Admin-Token",
            "NOVA_ADMIN_TOKEN",
            "@app.",
            "fastapi",
            "requests.",
        ):
            self.assertNotIn(forbidden, code)

    def test_it_registers_no_http_route(self):
        """Importer la CLI ne doit pas ajouter de route au serveur."""
        before = {route.path for route in main.app.routes}
        importlib.reload(admin_cli)
        self.assertEqual({route.path for route in main.app.routes}, before)


class FirstAdminRegressionTests(unittest.TestCase):
    """`grant` reste le chemin du premier administrateur — et de la récupération."""

    EMAIL = "operateur-cli@example.com"

    def setUp(self):
        with main.db() as conn:
            account = main.user_by_email(conn, main.nova_organization_id(), self.EMAIL)
            if account is None:
                main.create_user(conn, main.nova_organization_id(), self.EMAIL)

    def test_grant_assigns_a_security_role(self):
        code, output = run("grant", self.EMAIL, "organization_admin")
        self.assertEqual(code, 0)
        self.assertIn("organization_admin", output)

    def test_revoke_returns_the_account_to_member(self):
        run("grant", self.EMAIL, "organization_admin")
        code, output = run("revoke", self.EMAIL)
        self.assertEqual(code, 0)
        self.assertIn(main.SECURITY_ROLE_MEMBER, output)

    def test_list_shows_the_administrators(self):
        run("grant", self.EMAIL, "organization_admin")
        code, output = run("list")
        self.assertEqual(code, 0)
        self.assertIn(self.EMAIL, output)

    def test_an_unknown_role_is_refused(self):
        code, output = run("grant", self.EMAIL, "roi")
        self.assertEqual(code, 1)
        self.assertIn("refus", output)


class ControlPlaneModeTests(unittest.TestCase):
    """En mode Control Plane, l'organisation doit etre nommee.

    C'est la seule difference de comportement de la CLI entre les deux modes,
    et c'est celle qui compte : « le premier alex@example.com trouve » n'est
    pas une personne, c'est une coincidence. Une commande d'amorcage qui
    viserait l'organisation d'a cote serait une faute silencieuse.

    Le mode est lu sur `main` au moment de l'appel, ce qui rend le test
    deterministe sans redemarrer quoi que ce soit.
    """

    OTHER = "org-voisine-cli"

    def setUp(self):
        reset_discovery()
        with main.db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO organizations(id, slug, display_name, "
                "org_type, status, discovery_slug, discovery_enabled, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (self.OTHER, "voisine-cli", "Organisation voisine", "education",
                 "active", "slug-voisine", 0, 0.0))

    def tearDown(self):
        with main.db() as conn:
            conn.execute("DELETE FROM organizations WHERE id=?", (self.OTHER,))

    def control_plane(self):
        return patch.object(main, "NOVA_SERVER_MODE",
                            main.SERVER_MODE_CONTROL_PLANE)

    def test_discovery_without_an_organization_is_refused(self):
        with self.control_plane():
            code, output = run("discovery", "show")
        self.assertEqual(code, 1)
        self.assertIn("--organization", output)

    def test_discovery_targets_the_named_organization(self):
        """La sortie porte le slug de la **voisine**, pas celui de l'instance.

        C'est ce qui prouve le ciblage : lire l'organisation servie par
        l'instance aurait affiche `ecole-test`.
        """
        with self.control_plane():
            code, output = run("discovery", "show", "--organization", "voisine-cli")
        self.assertEqual(code, 0)
        self.assertIn("slug-voisine", output)
        self.assertNotIn("ecole-test", output)

    def test_enabling_touches_only_the_named_organization(self):
        with self.control_plane():
            code, _ = run("discovery", "enable", "--organization", self.OTHER,
                          "--endpoint", "https://voisine.example.com")
        self.assertEqual(code, 0)
        with main.db() as conn:
            neighbour = conn.execute(
                "SELECT discovery_enabled, service_endpoint FROM organizations "
                "WHERE id=?", (self.OTHER,)).fetchone()
        self.assertEqual(neighbour["discovery_enabled"], 1)
        self.assertEqual(neighbour["service_endpoint"],
                         "https://voisine.example.com")
        # L'organisation de l'instance n'a pas bouge.
        self.assertEqual(organization_row()["discovery_enabled"], 0)

    def test_the_same_endpoint_policy_applies_in_control_plane(self):
        """Le mode ne relache aucune validation."""
        with self.control_plane():
            code, _ = run("discovery", "enable", "--organization", self.OTHER,
                          "--endpoint", "https://127.0.0.1")
        self.assertEqual(code, 1)

    def test_dedicated_mode_still_needs_no_organization(self):
        code, _ = run("discovery", "show")
        self.assertEqual(code, 0)



class BootstrapFirstAdministratorTests(unittest.TestCase):
    """L'amorçage : nommer un administrateur là où aucun compte n'existe.

    C'est le cas de l'installateur, sur une organisation neuve : personne ne
    s'est encore connecté, donc `grant` seul n'a rien à promouvoir. Ces tests
    couvrent les quatre situations qui décident si l'installation livre un
    serveur pilotable : la promotion, sa répétition, son échec, et la saisie
    fautive qui ne doit jamais atteindre la base.
    """

    EMAIL = "premiere-admin@example.com"
    SECOND = "seconde-admin@example.com"

    def setUp(self):
        with main.db() as conn:
            for email in (self.EMAIL, self.SECOND, "TYPO@example.com"):
                conn.execute(
                    "DELETE FROM users WHERE organization_id=? AND normalized_email=?",
                    (main.nova_organization_id(), main.normalize_email(email)),
                )

    def account(self, email: str):
        with main.db() as conn:
            return main.user_by_email(conn, main.nova_organization_id(), email)

    def count(self, email: str) -> int:
        with main.db() as conn:
            return conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE organization_id=? "
                "AND normalized_email=?",
                (main.nova_organization_id(), main.normalize_email(email)),
            ).fetchone()["n"]

    # --- promotion réussie ------------------------------------------------

    def test_an_unknown_address_is_promoted_when_creation_is_asked_for(self):
        self.assertIsNone(self.account(self.EMAIL))
        code, output = run("grant", self.EMAIL, "organization_admin",
                           "--create-if-missing")
        self.assertEqual(code, 0, output)
        self.assertIn("organization_admin", output)
        self.assertEqual(self.account(self.EMAIL)["security_role"],
                         "organization_admin")

    def test_without_the_flag_an_unknown_address_is_still_refused(self):
        """Le défaut ne change pas : sur le chemin de récupération, une faute
        de frappe doit s'arrêter là plutôt que de fabriquer un compte."""
        code, output = run("grant", self.EMAIL, "organization_admin")
        self.assertEqual(code, 1)
        self.assertIn("aucun compte", output)
        self.assertIsNone(self.account(self.EMAIL))

    def test_the_created_account_is_an_ordinary_member_account(self):
        """Pas un compte parallèle : la même ligne que celle qu'une connexion
        aurait créée, avec le rôle en plus."""
        run("grant", self.EMAIL, "organization_admin", "--create-if-missing")
        account = self.account(self.EMAIL)
        self.assertEqual(account["organization_id"], main.nova_organization_id())
        self.assertEqual(account["normalized_email"],
                         main.normalize_email(self.EMAIL))
        self.assertEqual(main.account_status(account), "active")
        self.assertTrue(account["user_id"])

    def test_the_first_real_login_reuses_the_account_instead_of_doubling_it(self):
        """La propriété qui rend tout le dispositif acceptable.

        `touch_user` est ce que traverse une première connexion. Il doit
        retrouver le compte pré-créé par son adresse normalisée, et non en
        créer un second qui laisserait un administrateur fantôme d'un côté et
        la personne réelle de l'autre.
        """
        run("grant", self.EMAIL, "organization_admin", "--create-if-missing")
        created = self.account(self.EMAIL)
        main.touch_user(self.EMAIL.upper())
        self.assertEqual(self.count(self.EMAIL), 1)
        after = self.account(self.EMAIL)
        self.assertEqual(after["user_id"], created["user_id"])
        self.assertEqual(after["security_role"], "organization_admin")

    # --- rejouée : l'installation se relance, `update`, `reconfigure` ------

    def test_replaying_the_promotion_changes_nothing(self):
        for _ in range(3):
            code, _ = run("grant", self.EMAIL, "organization_admin",
                          "--create-if-missing")
            self.assertEqual(code, 0)
        self.assertEqual(self.count(self.EMAIL), 1)
        self.assertEqual(self.account(self.EMAIL)["security_role"],
                         "organization_admin")

    def test_replaying_keeps_the_same_identity(self):
        """Le `user_id` porte les sessions, les identités fédérées et l'audit.
        Une seconde exécution qui le changerait détacherait tout cela."""
        run("grant", self.EMAIL, "organization_admin", "--create-if-missing")
        first = self.account(self.EMAIL)["user_id"]
        run("grant", self.EMAIL, "organization_admin", "--create-if-missing")
        self.assertEqual(self.account(self.EMAIL)["user_id"], first)

    def test_two_administrators_coexist(self):
        run("grant", self.EMAIL, "organization_admin", "--create-if-missing")
        run("grant", self.SECOND, "organization_admin", "--create-if-missing")
        code, output = run("list")
        self.assertEqual(code, 0)
        self.assertIn(self.EMAIL, output)
        self.assertIn(self.SECOND, output)

    # --- échec correctement signalé ---------------------------------------

    def test_an_unknown_role_is_refused_and_creates_nothing(self):
        code, output = run("grant", self.EMAIL, "sous-chef", "--create-if-missing")
        self.assertEqual(code, 1)
        self.assertIn("refus", output)
        self.assertIsNone(self.account(self.EMAIL))

    def test_an_unknown_organization_is_refused_and_creates_nothing(self):
        code, output = run("grant", self.EMAIL, "organization_admin",
                           "--organization", "organisation-qui-n-existe-pas",
                           "--create-if-missing")
        self.assertEqual(code, 1)
        self.assertIn("refus", output)
        self.assertIsNone(self.account(self.EMAIL))

    def test_the_flag_is_refused_outside_grant(self):
        """Accepter le drapeau ailleurs en silence laisserait croire qu'il a
        fait quelque chose."""
        for argv in (["revoke", self.EMAIL], ["list"], ["discovery", "show"]):
            with self.subTest(argv=argv):
                code, output = run(*argv, "--create-if-missing")
                self.assertEqual(code, 2)
                self.assertIn("refus", output)

    def test_revoke_never_creates_an_account(self):
        code, _ = run("revoke", "jamais-vue@example.com")
        self.assertEqual(code, 1)
        with main.db() as conn:
            self.assertIsNone(main.user_by_email(
                conn, main.nova_organization_id(), "jamais-vue@example.com"))

    # --- adresse invalide, refusée avant la base --------------------------

    def test_a_malformed_address_is_refused_before_the_database(self):
        for candidate in ("", "   ", "alex", "alex@exemple", "alex@@exemple.fr",
                          "alex@exemple.f", "alex martin@exemple.fr",
                          "a@exemple.fr,b@exemple.fr", "<alex@exemple.fr>"):
            with self.subTest(candidate=candidate):
                code, output = run("grant", candidate, "organization_admin",
                                   "--create-if-missing")
                self.assertEqual(code, 1)
                self.assertIn("mal formée", output)

    def test_a_malformed_address_writes_nothing(self):
        with main.db() as conn:
            before = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        run("grant", "pas-une-adresse", "organization_admin", "--create-if-missing")
        with main.db() as conn:
            after = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        self.assertEqual(before, after)

    def test_ordinary_addresses_are_accepted_by_the_shape_check(self):
        """Trop strict serait pire que trop large : une adresse valide refusée
        empêcherait de nommer un administrateur."""
        for candidate in ("alex@exemple.fr", "alex.martin@sous.exemple.fr",
                          "alex+nova@exemple.fr", "a_b-c@exemple-lab.co.uk"):
            with self.subTest(candidate=candidate):
                self.assertTrue(admin_cli.email_is_well_formed(candidate))




class InheritedDomainDefaultTests(unittest.TestCase):
    """Le défaut de `ALLOWED_DOMAINS` ne doit plus jamais décider pour personne.

    `main.py` lit `NOVA_DOMAINS` et retombe, à défaut, sur les domaines d'un
    déploiement mono-tenant antérieur. L'installeur ne posait pas la variable :
    le conteneur servait donc ce défaut. Les administrateurs nommés par
    l'amorçage se connectaient quand même — leur compte existe déjà, et le
    rattachement précède le contrôle de domaine — mais **tout autre membre**
    échouait à sa première connexion. Une installation pouvait paraître réussie
    en ne testant que l'administrateur, et être inutilisable pour tous.

    Ces tests décrivent le contrat que l'installeur doit désormais honorer.
    """

    DECLARED = "exemple-lycee.test"
    INHERITED = "legacy-campus.test"

    def setUp(self):
        with main.db() as conn:
            conn.execute(
                "DELETE FROM users WHERE organization_id=? AND normalized_email LIKE ?",
                (main.nova_organization_id(), "%@" + self.DECLARED),
            )
            conn.execute(
                "DELETE FROM users WHERE organization_id=? AND normalized_email LIKE ?",
                (main.nova_organization_id(), "%@" + self.INHERITED),
            )

    def join(self, email: str, subject: str) -> str:
        """Ce que traverse une première connexion fédérée, sans identité connue."""
        return main.resolve_member_for_identity(
            email, main.nova_organization_id(), subject, "microsoft_entra")

    def test_a_member_of_the_declared_domain_can_join(self):
        """Le cas que l'installeur cassait : un membre ordinaire, hors du
        domaine hérité, dont l'organisation a bien déclaré le sien."""
        with patch.object(main, "ALLOWED_DOMAINS", [self.DECLARED]):
            resolved = self.join(f"eleve@{self.DECLARED}", "sujet-declare")
        self.assertEqual(resolved, f"eleve@{self.DECLARED}")
        with main.db() as conn:
            account = main.user_by_email(
                conn, main.nova_organization_id(), f"eleve@{self.DECLARED}")
        self.assertIsNotNone(account)

    def test_the_inherited_default_no_longer_admits_anyone(self):
        """Une fois le domaine réel déclaré, celui d'une autre organisation
        n'ouvre plus rien."""
        with patch.object(main, "ALLOWED_DOMAINS", [self.DECLARED]):
            with self.assertRaises(main.HTTPException) as refusal:
                self.join(f"quelquun@{self.INHERITED}", "sujet-herite")
        self.assertEqual(refusal.exception.status_code, 403)
        self.assertEqual(refusal.exception.detail, "MEMBERSHIP_NOT_FOUND")

    def test_without_the_declaration_the_organization_is_locked_out(self):
        """La situation exacte que produisait le compose sans `NOVA_DOMAINS` :
        le défaut hérité sert, et le membre réel est refusé."""
        with patch.object(main, "ALLOWED_DOMAINS", ["students.legacy-campus.test", self.INHERITED]):
            with self.assertRaises(main.HTTPException) as refusal:
                self.join(f"eleve@{self.DECLARED}", "sujet-sans-declaration")
        self.assertEqual(refusal.exception.detail, "MEMBERSHIP_NOT_FOUND")

    def test_the_administrator_hid_the_defect(self):
        """Pourquoi le défaut passait inaperçu : un compte déjà promu se
        rattache par son adresse, avant tout contrôle de domaine. Tester
        l'administrateur ne prouvait donc rien sur les autres."""
        admin = f"admin@{self.DECLARED}"
        run("grant", admin, "organization_admin", "--create-if-missing")
        with patch.object(main, "ALLOWED_DOMAINS", ["students.legacy-campus.test", self.INHERITED]):
            resolved = self.join(admin, "sujet-administrateur")
        self.assertEqual(resolved, admin)

    def test_several_declared_domains_are_all_honoured(self):
        declared = [self.DECLARED, "cadres." + self.DECLARED]
        with patch.object(main, "ALLOWED_DOMAINS", declared):
            for index, domain in enumerate(declared):
                with self.subTest(domain=domain):
                    resolved = self.join(f"membre{index}@{domain}", f"sujet-{index}")
                    self.assertEqual(resolved, f"membre{index}@{domain}")
        with main.db() as conn:
            for index, domain in enumerate(declared):
                conn.execute(
                    "DELETE FROM users WHERE organization_id=? AND normalized_email=?",
                    (main.nova_organization_id(), f"membre{index}@{domain}"),
                )



if __name__ == "__main__":
    unittest.main()
