#!/usr/bin/env python3
"""Decide, on the real GPU, which local vLLM runtime mode this machine can use.

vLLM 0.28's default model runner (GPUModelRunnerV2) allocates UvaBuffer, which
raises RuntimeError("UVA is not available") whenever vLLM reports pinned memory
as unavailable. Under WSL2 -- which is where Docker Desktop always runs on
Windows -- vLLM disables pinned memory by default, so the engine cannot start.

This probe runs inside the pinned language image, on the client's actual card,
and prints one JSON object on stdout. It never contacts the network and never
selects a remote runtime: the only outcomes are a local mode or none at all.

Exit codes:
  0  a compatible local runtime mode was found ("mode" names it)
  3  the GPU is usable but no local runtime mode works
  4  no usable CUDA GPU
"""

import json
import os
import sys
import traceback

# Modes are ordered by preference. The first that passes its real check wins.
#
# no_uva      vLLM's previous model runner. Needs no UVA and no pinned memory,
#             so it is unaffected by the WSL2 restriction. Preferred: it removes
#             the dependency rather than working around it.
# uva_pinned  The default runner, with pinned memory explicitly re-enabled.
#             Only selected when pinned memory and the accelerator view that
#             UvaBuffer builds both genuinely work on this machine.
MODES = ("no_uva", "uva_pinned")

MIN_COMPUTE_CAPABILITY = (7, 5)  # AWQ Marlin kernels require sm75 or newer.


def _probe_no_uva(report):
    """The opt-out must exist in this vLLM build, and the runner behind it too."""
    import vllm.envs as envs

    if "VLLM_USE_V2_MODEL_RUNNER" not in dir(envs):
        return False, "VLLM_USE_V2_MODEL_RUNNER is not supported by this vLLM build"
    try:
        # The runner this mode falls back to. If a future vLLM deletes it, the
        # opt-out is cosmetic and this mode must not be selected.
        import vllm.v1.worker.gpu_model_runner  # noqa: F401
    except Exception as exc:
        return False, "legacy GPU model runner unavailable: %s" % exc
    return True, "UVA not required"


def _probe_uva_pinned(report):
    """Perform exactly what UvaBuffer does, rather than trusting a flag."""
    import torch

    os.environ["VLLM_WSL2_ENABLE_PIN_MEMORY"] = "1"
    cpu = torch.zeros(1024, dtype=torch.int32, device="cpu", pin_memory=True)
    from vllm.utils.torch_utils import get_accelerator_view_from_cpu_tensor

    view = get_accelerator_view_from_cpu_tensor(cpu)
    cpu[0] = 4242
    torch.cuda.synchronize()
    if int(view[0].item()) != 4242:
        return False, "accelerator view did not observe the host write"
    return True, "pinned memory and accelerator view verified"


PROBES = {"no_uva": _probe_no_uva, "uva_pinned": _probe_uva_pinned}


def main():
    report = {"probe_version": 1, "mode": None, "modes": {}}
    try:
        import torch

        report["torch"] = torch.__version__
        try:
            import vllm

            report["vllm"] = vllm.__version__
        except Exception:
            report["vllm"] = None

        if not torch.cuda.is_available() or torch.cuda.device_count() < 1:
            report["error"] = "NO_CUDA_GPU"
            print(json.dumps(report))
            return 4

        props = torch.cuda.get_device_properties(0)
        capability = (props.major, props.minor)
        report["gpu"] = props.name
        report["compute_capability"] = "%d.%d" % capability
        report["vram_mib"] = props.total_memory // 1024 // 1024

        if capability < MIN_COMPUTE_CAPABILITY:
            report["error"] = "COMPUTE_CAPABILITY_TOO_LOW"
            print(json.dumps(report))
            return 3

        # Record why the default configuration fails here, so the installer log
        # explains the situation rather than only naming the workaround.
        try:
            from vllm.utils.platform_utils import is_uva_available

            report["uva_available_by_default"] = bool(is_uva_available())
        except Exception as exc:
            report["uva_available_by_default"] = None
            report["uva_probe_error"] = str(exc)

        for mode in MODES:
            try:
                ok, detail = PROBES[mode](report)
            except Exception as exc:
                ok, detail = False, "%s: %s" % (type(exc).__name__, exc)
            report["modes"][mode] = {"usable": ok, "detail": detail}
            if ok and report["mode"] is None:
                report["mode"] = mode

        if report["mode"] is None:
            report["error"] = "NO_COMPATIBLE_LOCAL_RUNTIME"
            print(json.dumps(report))
            return 3

        print(json.dumps(report))
        return 0
    except Exception:
        report["error"] = "PROBE_FAILED"
        report["traceback"] = traceback.format_exc()[-2000:]
        print(json.dumps(report))
        return 3


if __name__ == "__main__":
    sys.exit(main())
