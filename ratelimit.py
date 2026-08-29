"""Limitation de débit partagée entre processus.

## Le défaut que ce module corrige

Deux limiteurs existaient, l'un dans `main.py` pour le Connector, l'autre dans
`scim.py` pour le provisioning. Tous deux comptaient dans un dictionnaire en
mémoire de processus. Derrière quatre workers, une limite annoncée à 300 appels
par minute en autorisait mille deux cents — et la console affichait la
protection comme si elle était appliquée. Une limite qu'on croit avoir est pire
qu'une limite qu'on sait ne pas avoir.

## Ce qui remplace

Un compteur dans Redis, partagé par tous les workers. La fenêtre est glissante
et implémentée en Lua : la lecture, la purge et l'écriture se produisent en un
seul aller-retour atomique. Un `GET` suivi d'un `SET` depuis quatre processus
laisserait passer exactement ce que le limiteur est censé empêcher.

## Ce qu'on ne fait pas

Pas de limiteur « distribué » bâti sur SQLite. Compter dans la base de données
que l'on protège invite le déni de service à passer par le compteur lui-même, et
ne survit pas au moment où plusieurs machines servent le trafic.

## Que faire quand Redis est absent

Deux politiques, choisies par le point d'appel et jamais par défaut :

- `FAIL_CLOSED` — refuser. C'est ce que prennent l'authentification, le
  step-up, la création de credentials et l'enrôlement : sans compteur, on ne
  peut pas distinguer un utilisateur d'un attaquant qui essaie mille fois.
- `FAIL_OPEN` — laisser passer, en le signalant. Pour un trafic de
  synchronisation où refuser tout le monde coûterait plus cher que de compter
  faux pendant une panne Redis.

En développement, sans Redis configuré, un compteur local prend le relais : il
ne partage rien, et le dit.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any


FAIL_CLOSED = "fail_closed"
FAIL_OPEN = "fail_open"

# Classes de routes. Elles servent d'étiquette de métrique : une étiquette par
# classe, jamais par tenant ni par credential — voir `docs` sur la cardinalité.
CLASS_AUTH = "auth"
CLASS_ADMIN = "admin"
CLASS_SCIM = "scim"
CLASS_CONNECTOR = "connector"
CLASS_DEPLOYMENT = "deployment"
CLASS_LEARNING = "learning"


class RateLimitExceeded(Exception):
    """Quota dépassé. Porte la classe, jamais l'identité concernée."""

    def __init__(self, route_class: str, retry_after: int) -> None:
        super().__init__(f"rate limit exceeded for {route_class}")
        self.route_class = route_class
        self.retry_after = retry_after


class LimiterUnavailable(Exception):
    """Backend partagé injoignable, sur une route qui exige de compter."""


@dataclass(frozen=True)
class Quota:
    limit: int
    window_seconds: float
    route_class: str
    on_unavailable: str = FAIL_CLOSED


# Fenêtre glissante, atomique. `ZREMRANGEBYSCORE` purge ce qui est sorti de la
# fenêtre, `ZCARD` compte ce qui reste, et l'ajout n'a lieu que si la place
# existe. Les trois dans le même script : entre deux d'entre elles, quatre
# workers auraient le temps de tous conclure qu'il reste de la place.
_SLIDING_WINDOW = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local used = redis.call('ZCARD', key)
if used >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry = window
  if oldest[2] then retry = (tonumber(oldest[2]) + window) - now end
  if retry < 1 then retry = 1 end
  return {0, used, math.ceil(retry)}
end
redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, math.ceil(window * 1000))
return {1, used + 1, 0}
"""


class _LocalWindow:
    """Repli de développement. Ne partage rien, et l'annonce."""

    shared = False

    def __init__(self) -> None:
        self._buckets: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window: float, now: float) -> tuple[bool, int]:
        with self._lock:
            bucket = self._buckets.setdefault(key, [])
            cutoff = now - window
            bucket[:] = [seen for seen in bucket if seen >= cutoff]
            if len(bucket) >= limit:
                retry = max(1, int(bucket[0] + window - now) + 1)
                return False, retry
            bucket.append(now)
            return True, 0

    def healthy(self) -> bool:
        return True

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


class _RedisWindow:
    """Compteur partagé. La seule implémentation utilisable en production."""

    shared = True

    def __init__(self, url: str) -> None:
        import redis

        self._client = redis.Redis.from_url(
            url,
            socket_connect_timeout=float(os.getenv("NOVA_REDIS_TIMEOUT", "2")),
            socket_timeout=float(os.getenv("NOVA_REDIS_TIMEOUT", "2")),
            decode_responses=True,
        )
        self._script = self._client.register_script(_SLIDING_WINDOW)
        self._sequence = 0
        self._lock = threading.Lock()

    def _member(self, now: float) -> str:
        # Un membre unique par appel : deux requêtes dans la même microseconde
        # doivent compter pour deux, et un score seul ne le garantirait pas.
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        return f"{os.getpid()}-{sequence}-{now}"

    def allow(self, key: str, limit: int, window: float, now: float) -> tuple[bool, int]:
        allowed, _used, retry = self._script(
            keys=[key], args=[now, window, limit, self._member(now)]
        )
        return bool(int(allowed)), int(retry)

    def healthy(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:
            return False

    def reset(self) -> None:
        # Réservé aux tests et aux recettes : jamais appelé par le service.
        for key in self._client.scan_iter(match="nova:rl:*", count=500):
            self._client.delete(key)


class RateLimiter:
    """Façade. Un objet par processus."""

    def __init__(self, redis_url: str | None = None) -> None:
        url = (redis_url if redis_url is not None else os.getenv("NOVA_REDIS_URL", "")).strip()
        self._url = url
        self._backend: Any
        if url:
            self._backend = _RedisWindow(url)
        else:
            self._backend = _LocalWindow()

    @property
    def shared(self) -> bool:
        """Vrai seulement si le compteur est réellement partagé entre processus."""
        return bool(getattr(self._backend, "shared", False))

    @property
    def configured(self) -> bool:
        return bool(self._url)

    def healthy(self) -> bool:
        return self._backend.healthy()

    def check(self, scope: str, identity: str, quota: Quota, now: float | None = None) -> None:
        """Consomme un jeton, ou lève.

        `scope` dit ce qui est compté (`credential`, `tenant`, `ip`,
        `principal`), `identity` qui est compté. Les deux forment la clé ; ni
        l'un ni l'autre ne devient jamais une étiquette de métrique.
        """
        moment = time.time() if now is None else now
        key = f"nova:rl:{quota.route_class}:{scope}:{identity}"
        try:
            allowed, retry = self._backend.allow(key, quota.limit, quota.window_seconds, moment)
        except Exception as error:
            # Le backend est tombé. La décision n'appartient pas à ce module :
            # elle a été prise à la déclaration du quota, par la route.
            if quota.on_unavailable == FAIL_OPEN:
                return
            raise LimiterUnavailable(
                f"rate limit backend unavailable for {quota.route_class}"
            ) from error
        if not allowed:
            raise RateLimitExceeded(quota.route_class, retry)

    def reset(self) -> None:
        self._backend.reset()


# --------------------------------------------------------------------- quotas

# Les valeurs reprennent celles déjà appliquées, pour que le passage au
# compteur partagé ne change pas la politique en même temps que la mécanique.
# Un seul changement à la fois : c'est la mécanique qui est en cause ici.
SCIM_PER_CREDENTIAL = Quota(300, 60.0, CLASS_SCIM, FAIL_CLOSED)
SCIM_PER_TENANT = Quota(900, 60.0, CLASS_SCIM, FAIL_CLOSED)

# Synchronisation du Connector : refuser tout le monde pendant une panne Redis
# couperait la configuration de chaque organisation. On compte faux plutôt que
# de tout arrêter — et on l'écrit ici pour que ce soit une décision, pas un
# oubli.
CONNECTOR_SYNC = Quota(120, 60.0, CLASS_CONNECTOR, FAIL_OPEN)
# L'enrôlement, lui, est un chemin d'authentification.
CONNECTOR_ENROLL = Quota(5, 60.0, CLASS_CONNECTOR, FAIL_CLOSED)
CONNECTOR_UPDATE = Quota(30, 60.0, CLASS_CONNECTOR, FAIL_CLOSED)

AUTH_START = Quota(10, 60.0, CLASS_AUTH, FAIL_CLOSED)
AUTH_VERIFY = Quota(20, 60.0, CLASS_AUTH, FAIL_CLOSED)
ADMIN_STEP_UP = Quota(10, 60.0, CLASS_ADMIN, FAIL_CLOSED)
ADMIN_CREDENTIAL_CREATE = Quota(20, 60.0, CLASS_ADMIN, FAIL_CLOSED)
DEPLOYMENT_VERIFY = Quota(60, 60.0, CLASS_DEPLOYMENT, FAIL_CLOSED)
# Un exercice Learn appelle un moteur d'inference. La borne protege le
# moteur, pas un secret : un membre qui pratique honnetement ne l'atteint
# jamais. `FAIL_CLOSED` — si le compteur partage tombe, mieux vaut refuser
# un exercice que laisser une boucle saturer le runtime de tout le monde.
LEARNING_EXERCISE = Quota(20, 60.0, CLASS_LEARNING, FAIL_CLOSED)

ALL_QUOTAS = {
    "scim_per_credential": SCIM_PER_CREDENTIAL,
    "scim_per_tenant": SCIM_PER_TENANT,
    "connector_sync": CONNECTOR_SYNC,
    "connector_enroll": CONNECTOR_ENROLL,
    "connector_update": CONNECTOR_UPDATE,
    "auth_start": AUTH_START,
    "auth_verify": AUTH_VERIFY,
    "admin_step_up": ADMIN_STEP_UP,
    "admin_credential_create": ADMIN_CREDENTIAL_CREATE,
    "deployment_verify": DEPLOYMENT_VERIFY,
    "learning_exercise": LEARNING_EXERCISE,
}
