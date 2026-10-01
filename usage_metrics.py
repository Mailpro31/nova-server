"""L'historique que la console trace : usage horaire, latence, etat machine.

Etat : seule la mesure est ecrite. `sample_host()` releve l'etat de la machine
a un instant donne, sans effet de bord ni acces a la base, ce qui le rend
testable seul.

Restent a ecrire, et rien ne les appelle encore : la boucle qui echantillonne
toutes les `SAMPLE_INTERVAL_SECONDS`, le repli du detail horaire en trace
mensuelle, et la purge de ce qui a depasse sa duree de conservation (les trois
durees sont deja nommees dans `main.py`).

## Pourquoi un echantillonneur ici et pas Prometheus

Sur une machine unique — le cas d'un serveur d'etablissement — Prometheus et
Grafana ajoutent trois conteneurs, une seconde authentification et une seconde
esthetique, pour stocker cinq series. La console sait deja authentifier un
administrateur et dessiner ; il lui manquait l'historique. On le lui donne, et
`/api/metrics` reste expose pour une supervision d'etablissement qui existerait
deja.

## Ce qui n'est jamais releve

Aucun contenu, aucune adresse IP, aucun nom de processus. Le releve machine
porte des nombres : charge, memoire, temperature. Savoir *quel* logiciel occupe
le processeur releverait de l'inventaire, pas de la mesure — et un tableau de
bord d'usage n'a pas besoin de devenir un outil de surveillance du poste.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass


# Une minute suffit a voir un pic d'usage et divise par deux le volume
# conserve par rapport a trente secondes. Plus fin ne repondrait a aucune
# question que la console pose.
SAMPLE_INTERVAL_SECONDS = 60.0

# Au-dela, `nvidia-smi` est considere comme absent plutot qu'attendu : un
# releve qui bloque la boucle est pire qu'un releve manquant.
NVIDIA_SMI_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class HostSample:
    sampled_at: str
    cpu_percent: float | None
    ram_used_gb: float | None
    ram_total_gb: float | None
    gpu_percent: float | None
    vram_used_gb: float | None
    vram_total_gb: float | None
    gpu_temperature_c: float | None
    gpu_power_w: float | None


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _cpu_and_ram() -> tuple[float | None, float | None, float | None]:
    """Charge processeur et memoire, ou `None` quand on ne sait pas lire.

    Volontairement sans dependance : `psutil` donnerait la meme chose au prix
    d'un paquet de plus dans une image qui en compte peu.
    """
    cpu = ram_used = ram_total = None
    try:
        if platform.system() == "Linux":
            with open("/proc/meminfo", encoding="utf-8") as handle:
                fields = {}
                for line in handle:
                    parts = line.split()
                    if len(parts) >= 2:
                        fields[parts[0].rstrip(":")] = int(parts[1])
            total_kib = fields.get("MemTotal")
            available_kib = fields.get("MemAvailable")
            if total_kib:
                ram_total = total_kib / (1024**2)
                if available_kib is not None:
                    ram_used = (total_kib - available_kib) / (1024**2)
            # `loadavg` sur une minute, ramene au nombre de coeurs : ce n'est
            # pas la charge instantanee, mais c'est la seule mesure lisible
            # sans echantillonner deux fois /proc/stat.
            cores = os.cpu_count() or 1
            with open("/proc/loadavg", encoding="utf-8") as handle:
                one_minute = float(handle.read().split()[0])
            cpu = min(100.0, one_minute / cores * 100.0)
    except (OSError, ValueError, IndexError):
        return None, None, None
    return _round(cpu), _round(ram_used, 2), _round(ram_total, 2)


def _gpu() -> tuple[float | None, float | None, float | None, float | None, float | None]:
    """Etat de la carte NVIDIA, ou des `None` s'il n'y en a pas.

    L'absence de carte n'est pas une panne : un serveur sans GPU sert des
    modeles plus petits, et le tableau de bord doit le montrer sans erreur.
    """
    binary = shutil.which("nvidia-smi")
    if not binary:
        return None, None, None, None, None
    try:
        completed = subprocess.run(
            [
                binary,
                "--query-gpu=utilization.gpu,memory.used,memory.total,"
                "temperature.gpu,power.draw",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=NVIDIA_SMI_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None, None, None, None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None, None, None, None, None
    try:
        parts = [p.strip() for p in completed.stdout.splitlines()[0].split(",")]
        used, total = float(parts[1]) / 1024, float(parts[2]) / 1024
        return (
            _round(float(parts[0])),
            _round(used, 2),
            _round(total, 2),
            _round(float(parts[3])),
            _round(float(parts[4])),
        )
    except (IndexError, ValueError):
        return None, None, None, None, None


def sample_host(now: float | None = None) -> HostSample:
    """Un releve, horodate a la seconde en UTC."""
    moment = time.gmtime(now if now is not None else time.time())
    cpu, ram_used, ram_total = _cpu_and_ram()
    gpu, vram_used, vram_total, temperature, power = _gpu()
    return HostSample(
        sampled_at=time.strftime("%Y-%m-%dT%H:%M:%S", moment),
        cpu_percent=cpu,
        ram_used_gb=ram_used,
        ram_total_gb=ram_total,
        gpu_percent=gpu,
        vram_used_gb=vram_used,
        vram_total_gb=vram_total,
        gpu_temperature_c=temperature,
        gpu_power_w=power,
    )
