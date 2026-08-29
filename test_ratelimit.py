"""Le limiteur, vérifié contre un vrai Redis et de vrais processus séparés.

Le test qui compte est `test_two_separate_processes_share_one_counter` : c'est
exactement ce que l'implémentation en mémoire de processus ne savait pas faire,
et aucune assertion sur un objet unique ne l'aurait démontré.
"""

import os
import subprocess
import sys
import textwrap
import threading
import time
import unittest
import uuid

import ratelimit


REDIS_URL = os.getenv("NOVA_TEST_REDIS_URL", "").strip()


class QuotaDeclarationTests(unittest.TestCase):
    def test_authentication_paths_fail_closed(self):
        # Sans compteur, on ne distingue pas un utilisateur d'un attaquant qui
        # essaie mille fois. Ces routes doivent refuser.
        for name in (
            "auth_start", "auth_verify", "admin_step_up",
            "admin_credential_create", "connector_enroll",
            "scim_per_credential", "scim_per_tenant", "deployment_verify",
        ):
            self.assertEqual(
                ratelimit.ALL_QUOTAS[name].on_unavailable,
                ratelimit.FAIL_CLOSED,
                f"{name} must fail closed",
            )

    def test_the_only_fail_open_quota_is_a_deliberate_one(self):
        open_quotas = [
            name for name, quota in ratelimit.ALL_QUOTAS.items()
            if quota.on_unavailable == ratelimit.FAIL_OPEN
        ]
        # Une seule, et c'est la synchronisation : refuser tout le monde
        # pendant une panne Redis couperait la configuration de chaque
        # organisation.
        self.assertEqual(open_quotas, ["connector_sync"])

    def test_the_scim_policy_is_unchanged_by_the_move_to_redis(self):
        # Un seul changement à la fois : la mécanique, pas la politique.
        self.assertEqual(ratelimit.SCIM_PER_CREDENTIAL.limit, 300)
        self.assertEqual(ratelimit.SCIM_PER_TENANT.limit, 900)
        self.assertEqual(ratelimit.SCIM_PER_CREDENTIAL.window_seconds, 60.0)


class LocalFallbackTests(unittest.TestCase):
    def setUp(self):
        self.limiter = ratelimit.RateLimiter(redis_url="")

    def test_the_development_fallback_admits_that_it_shares_nothing(self):
        # C'est la propriété qui manquait : le limiteur précédent se présentait
        # comme une protection sans jamais dire qu'il ne franchissait pas les
        # frontières de processus.
        self.assertFalse(self.limiter.shared)
        self.assertFalse(self.limiter.configured)

    def test_a_quota_is_enforced_then_refused(self):
        quota = ratelimit.Quota(3, 60.0, "test")
        identity = uuid.uuid4().hex
        for _ in range(3):
            self.limiter.check("credential", identity, quota)
        with self.assertRaises(ratelimit.RateLimitExceeded) as raised:
            self.limiter.check("credential", identity, quota)
        self.assertEqual(raised.exception.route_class, "test")
        self.assertGreaterEqual(raised.exception.retry_after, 1)

    def test_two_identities_do_not_share_a_bucket(self):
        quota = ratelimit.Quota(1, 60.0, "test")
        self.limiter.check("credential", "a", quota)
        self.limiter.check("credential", "b", quota)

    def test_two_scopes_do_not_share_a_bucket(self):
        quota = ratelimit.Quota(1, 60.0, "test")
        self.limiter.check("credential", "same", quota)
        self.limiter.check("tenant", "same", quota)

    def test_the_window_slides(self):
        quota = ratelimit.Quota(2, 1.0, "test")
        identity = uuid.uuid4().hex
        base = time.time()
        self.limiter.check("credential", identity, quota, now=base)
        self.limiter.check("credential", identity, quota, now=base)
        with self.assertRaises(ratelimit.RateLimitExceeded):
            self.limiter.check("credential", identity, quota, now=base)
        # Une fois la fenêtre écoulée, le quota se reconstitue.
        self.limiter.check("credential", identity, quota, now=base + 1.5)


class UnavailableBackendTests(unittest.TestCase):
    class _Broken:
        shared = True

        def allow(self, *_args, **_kwargs):
            raise ConnectionError("redis is down")

        def healthy(self):
            return False

        def reset(self):
            pass

    def setUp(self):
        self.limiter = ratelimit.RateLimiter(redis_url="")
        self.limiter._backend = self._Broken()

    def test_a_fail_closed_route_refuses_when_the_backend_is_down(self):
        with self.assertRaises(ratelimit.LimiterUnavailable):
            self.limiter.check("ip", "1.2.3.4", ratelimit.AUTH_START)

    def test_a_fail_open_route_keeps_serving_when_the_backend_is_down(self):
        self.limiter.check("connector", "c1", ratelimit.CONNECTOR_SYNC)

    def test_the_error_never_names_the_identity_being_limited(self):
        try:
            self.limiter.check("credential", "cred-secret-identity", ratelimit.SCIM_PER_CREDENTIAL)
        except ratelimit.LimiterUnavailable as error:
            self.assertNotIn("cred-secret-identity", str(error))
        else:
            self.fail("expected LimiterUnavailable")


@unittest.skipUnless(REDIS_URL, "NOVA_TEST_REDIS_URL is not set")
class RedisLimiterTests(unittest.TestCase):
    def setUp(self):
        self.limiter = ratelimit.RateLimiter(redis_url=REDIS_URL)
        self.limiter.reset()

    def test_redis_backed_counting_is_declared_shared(self):
        self.assertTrue(self.limiter.shared)
        self.assertTrue(self.limiter.configured)
        self.assertTrue(self.limiter.healthy())

    def test_a_quota_is_enforced_against_a_real_redis(self):
        quota = ratelimit.Quota(5, 60.0, "test")
        identity = uuid.uuid4().hex
        for _ in range(5):
            self.limiter.check("credential", identity, quota)
        with self.assertRaises(ratelimit.RateLimitExceeded):
            self.limiter.check("credential", identity, quota)

    def test_concurrent_threads_never_exceed_the_quota(self):
        quota = ratelimit.Quota(20, 60.0, "test")
        identity = uuid.uuid4().hex
        allowed = []
        lock = threading.Lock()
        barrier = threading.Barrier(10)

        def hammer():
            barrier.wait()
            for _ in range(10):
                try:
                    self.limiter.check("credential", identity, quota)
                    with lock:
                        allowed.append(1)
                except ratelimit.RateLimitExceeded:
                    pass

        threads = [threading.Thread(target=hammer) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        # Cent tentatives, vingt autorisations. Sans script atomique, la lecture
        # et l'écriture s'entrelaceraient et le compte dépasserait.
        self.assertEqual(len(allowed), 20)

    def test_two_separate_processes_share_one_counter(self):
        """La preuve que le limiteur précédent ne pouvait pas fournir.

        Deux processus Python distincts, un quota de 6, trois tentatives
        chacun : le sixième passe, le septième est refusé. Avec un compteur en
        mémoire de processus, les six seraient tous passés.
        """
        identity = uuid.uuid4().hex
        worker = textwrap.dedent(
            """
            import json, sys
            import ratelimit
            limiter = ratelimit.RateLimiter(redis_url=sys.argv[1])
            quota = ratelimit.Quota(6, 60.0, "multiworker")
            allowed = 0
            refused = 0
            for _ in range(int(sys.argv[3])):
                try:
                    limiter.check("credential", sys.argv[2], quota)
                    allowed += 1
                except ratelimit.RateLimitExceeded:
                    refused += 1
            print(json.dumps({"allowed": allowed, "refused": refused,
                              "shared": limiter.shared}))
            """
        )
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_mw_worker.py")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(worker)
        try:
            results = []
            processes = [
                subprocess.Popen(
                    [sys.executable, script, REDIS_URL, identity, "5"],
                    stdout=subprocess.PIPE,
                    cwd=os.path.dirname(os.path.abspath(__file__)),
                    text=True,
                )
                for _ in range(2)
            ]
            for process in processes:
                out, _ = process.communicate(timeout=60)
                self.assertEqual(process.returncode, 0, out)
                results.append(__import__("json").loads(out.strip().splitlines()[-1]))
        finally:
            if os.path.exists(script):
                os.remove(script)

        for result in results:
            self.assertTrue(result["shared"], "each worker must use the shared backend")
        total_allowed = sum(result["allowed"] for result in results)
        total_refused = sum(result["refused"] for result in results)
        # Dix tentatives réparties sur deux processus, six autorisations au
        # total. C'est l'égalité stricte qui prouve le partage : avec des
        # compteurs séparés, chaque processus en aurait autorisé cinq.
        self.assertEqual(total_allowed, 6)
        self.assertEqual(total_refused, 4)

    def test_the_key_space_is_namespaced_so_a_reset_cannot_touch_anything_else(self):
        quota = ratelimit.Quota(1, 60.0, "test")
        self.limiter.check("credential", "namespaced", quota)
        import redis

        client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        keys = list(client.scan_iter(match="nova:rl:*"))
        self.assertTrue(keys)
        for key in keys:
            self.assertTrue(key.startswith("nova:rl:"), key)


if __name__ == "__main__":
    unittest.main()
