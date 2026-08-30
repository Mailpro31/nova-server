import json
import re
from pathlib import Path


ROOT = Path(__file__).parent / "tools" / "organization-lab"


def manifest():
    return json.loads((ROOT / "profiles.manifest.json").read_text(encoding="utf-8"))


def select(vram_mib: int | None):
    profiles = manifest()["profiles"]
    if vram_mib is None:
        return next(p for p in profiles if p["id"] == "LAB_CPU")
    matches = [
        p for p in profiles
        if p.get("gpu")
        and p["minimum_vram_mib"] <= vram_mib <= p["maximum_vram_mib"]
    ]
    assert len(matches) == 1
    return matches[0]


def test_profile_boundaries_and_rtx_2060():
    assert select(None)["id"] == "LAB_CPU"
    assert select(4096)["id"] == "LAB_GPU_4GB"
    assert select(6144)["id"] == "LAB_GPU_6GB"
    assert select(8192)["id"] == "LAB_GPU_8GB"
    assert select(12288)["id"] == "LAB_GPU_12GB"
    assert select(16384)["id"] == "LAB_GPU_16GB"
    assert select(24576)["id"] == "LAB_GPU_24GB_PLUS"


def test_profiles_cover_all_vram_without_overlap():
    profiles = sorted((p for p in manifest()["profiles"] if p.get("gpu")), key=lambda p: p["minimum_vram_mib"])
    assert profiles[0]["minimum_vram_mib"] == 1
    for left, right in zip(profiles, profiles[1:]):
        assert left["maximum_vram_mib"] + 1 == right["minimum_vram_mib"]


def test_rtx_2060_does_not_receive_14b():
    profile = select(6144)
    assert "14B" not in profile["text_model"]
    assert profile["speech_model"] != "large-v3-turbo"
    assert profile["max_concurrent_text"] == 1


def test_distribution_has_no_customer_github_or_huggingface_dependency():
    setup = (ROOT / "NovaOrganizationServerSetup.ps1").read_text(encoding="utf-8").lower()
    assert "git clone" not in setup
    assert "gh auth" not in setup
    assert "github.com" not in setup
    assert "huggingface.co" not in setup
    assert "releases.novaspeak.app" in setup


def test_artifacts_are_hash_or_digest_checked_before_execution():
    setup = (ROOT / "NovaOrganizationServerSetup.ps1").read_text(encoding="utf-8")
    hash_check = setup.index("ARTIFACT_HASH_MISMATCH")
    docker_load = setup.index("@('load','--input',$path)")
    assert hash_check < docker_load
    assert "OCI_REFERENCE_NOT_IMMUTABLE" in setup
    assert "OCI_DIGEST_VERIFICATION_FAILED" in setup
    assert "@sha256:" in setup


def test_manifest_origin_is_pinned_and_signature_failure_stops():
    setup = (ROOT / "NovaOrganizationServerSetup.ps1").read_text(encoding="utf-8")
    assert "$script:AllowedManifestOrigin = 'https://releases.novaspeak.app'" in setup
    assert "MANIFEST_ORIGIN_NOT_PINNED" in setup
    assert "MANIFEST_SIGNATURE_INVALID" in setup
    assert "RELEASE_PUBLIC_KEY_NOT_CONFIGURED" in setup


def test_generated_files_and_secrets_are_gitignored():
    ignored = (Path(__file__).parent / ".gitignore").read_text(encoding="utf-8")
    assert "tools/organization-lab/generated/" in ignored
    assert "tools/organization-lab/state/" in ignored
    assert "tools/organization-lab/dist/" in ignored
    setup = (ROOT / "NovaOrganizationServerSetup.ps1").read_text(encoding="utf-8")
    assert "RandomNumberGenerator" in setup
    assert "postgres-password.txt" in setup


def test_desktop_managed_config_is_not_written():
    setup = (ROOT / "NovaOrganizationServerSetup.ps1").read_text(encoding="utf-8").lower()
    assert "organization.json" not in setup
    assert "campus-config.json" not in setup
    assert "api.novaspeak.app" not in setup


def test_logs_redact_sensitive_assignments():
    setup = (ROOT / "NovaOrganizationServerSetup.ps1").read_text(encoding="utf-8")
    assert re.search(r"password\|token\|secret\|credential", setup, re.IGNORECASE)
    assert "[REDACTED]" in setup


def build_substituted_setup(key: str = "<RSAKeyValue><Modulus>" + "A" * 512 + "</Modulus><Exponent>AQAB</Exponent></RSAKeyValue>") -> str:
    """Apply the substitution build-installer.ps1 performs, so the tests below
    inspect the script as it is actually shipped rather than as it is stored."""
    source = (ROOT / "NovaOrganizationServerSetup.ps1").read_text(encoding="utf-8")
    return source.replace("__NOVA_RELEASE_PUBLIC_KEY_XML__", key)


def test_release_key_placeholder_has_exactly_one_substitution_site():
    # The build replaces every occurrence. A second occurrence anywhere means
    # live logic gets rewritten to contain the key. That is what shipped in
    # 0.1.0: the guard became a comparison of the key against itself and threw
    # RELEASE_PUBLIC_KEY_NOT_CONFIGURED precisely when the key was correct.
    source = (ROOT / "NovaOrganizationServerSetup.ps1").read_text(encoding="utf-8")
    assert source.count("__NOVA_RELEASE_PUBLIC_KEY_XML__") == 1


def test_shipped_guard_does_not_compare_the_key_against_itself():
    shipped = build_substituted_setup()
    assert "-eq '<RSAKeyValue>" not in shipped
    assert "RELEASE_PUBLIC_KEY_NOT_CONFIGURED" in shipped


def test_shipped_script_embeds_the_key_exactly_once():
    key = "<RSAKeyValue><Modulus>" + "A" * 512 + "</Modulus><Exponent>AQAB</Exponent></RSAKeyValue>"
    assert build_substituted_setup(key).count(key) == 1


def test_key_guard_is_a_structural_check_not_a_placeholder_comparison():
    source = (ROOT / "NovaOrganizationServerSetup.ps1").read_text(encoding="utf-8")
    assert "function Assert-ReleasePublicKeyConfigured" in source
    assert "$ReleasePublicKeyXml -notmatch" in source
    # still fails closed when unbuilt: the placeholder is not a key
    assert "$ReleasePublicKeyXml -eq '__NOVA_RELEASE_PUBLIC_KEY_XML__'" not in source


def test_selftest_action_exists_and_needs_no_network_or_key_file():
    source = (ROOT / "NovaOrganizationServerSetup.ps1").read_text(encoding="utf-8")
    assert "'selftest'" in source
    assert "RELEASE_PUBLIC_KEY_OK" in source
    # the selftest must run before any privileged directory work
    assert source.index("$Action -eq 'selftest'") < source.index("try {\n    Initialize-Directories")


def test_build_refuses_to_ship_an_installer_without_an_embedded_key():
    build = (ROOT / "build-installer.ps1").read_text(encoding="utf-8")
    assert "Expected exactly one release key placeholder" in build
    assert "Release key placeholder survived substitution." in build
    assert "Compiled installer compares the release key against itself." in build
    assert "GetManifestResourceStream('Nova.Setup.ps1')" in build


def test_signature_verification_is_not_bypassable():
    source = (ROOT / "NovaOrganizationServerSetup.ps1").read_text(encoding="utf-8")
    # the verified-manifest path must still reject a bad signature outright
    assert "MANIFEST_SIGNATURE_INVALID" in source
    assert "Assert-ReleasePublicKeyConfigured" in source
    assert "VerifyData" in source


def setup_source() -> str:
    return (ROOT / "NovaOrganizationServerSetup.ps1").read_text(encoding="utf-8")


def test_docker_inspect_format_has_no_backtick():
    # A backtick opens a raw quoted string in a Go template. The previous format
    # was {{join .RepoDigests "<backtick>n"}}, intended as a PowerShell newline
    # escape but sitting in a single-quoted string, so docker exited 64 with
    # "unterminated raw quoted string" and the caller reported that as
    # OCI_DIGEST_VERIFICATION_FAILED on every machine.
    source = setup_source()
    for line in source.splitlines():
        if "--format" in line and "docker.exe" in line:
            assert "`" not in line, f"backtick in a docker template: {line.strip()}"


def test_inspect_failure_is_distinct_from_digest_mismatch():
    source = setup_source()
    assert "OCI_INSPECT_FAILED" in source
    # the mismatch branch must no longer double as the inspect-failed branch
    assert "$digests.ExitCode -ne 0 -or" not in source


def test_oci_verification_still_fails_closed():
    source = setup_source()
    for code in ("OCI_REFERENCE_NOT_IMMUTABLE", "OCI_REGISTRY_NOT_APPROVED",
                 "OCI_PULL_FAILED", "OCI_DIGEST_VERIFICATION_FAILED",
                 "OCI_PLATFORM_UNEXPECTED", "OCI_TAG_FAILED"):
        assert code in source
    # the pull must still be by immutable digest, never a tag
    assert "@sha256:" in source


def test_client_requires_a_single_platform_image():
    source = setup_source()
    assert "'{{.Os}}/{{.Architecture}}'" in source
    assert "linux/amd64" in source


def test_release_workflow_pins_platform_manifest_digests():
    workflow = (Path(__file__).parent / ".github" / "workflows"
                / "organization-lab-release.yml").read_text(encoding="utf-8")
    assert "Resolve linux/amd64 manifest digests" in workflow
    # the manifest must be built from the resolved platform digests, not the
    # index digests the build step emits
    assert "SERVER_DIGEST: ${{ steps.dependencies.outputs.server }}" in workflow
    assert "SPEECH_DIGEST: ${{ steps.dependencies.outputs.speech }}" in workflow
    assert "LANGUAGE_DIGEST: ${{ steps.dependencies.outputs.language }}" in workflow
    assert "SERVER_DIGEST: ${{ needs.server.outputs.digest }}" not in workflow


def test_release_workflow_verifies_published_images():
    workflow = (Path(__file__).parent / ".github" / "workflows"
                / "organization-lab-release.yml").read_text(encoding="utf-8")
    assert "verify-release-images.py" in workflow
    assert "needs: publish" in workflow


def test_image_verifier_checks_manifest_config_and_layers():
    verifier = (ROOT / "verify-release-images.py").read_text(encoding="utf-8")
    assert "anonymous_token" in verifier
    assert "manifest bytes hash to" in verifier
    assert "config blob hashes to" in verifier
    assert "layer {index} hashes to" in verifier
    assert "is an image index, not a platform manifest" in verifier
    assert "REQUIRED_NOVA_IMAGES" in verifier


def test_docker_detection_does_not_depend_only_on_the_daemon():
    # DockerAvailable is "docker version" succeeding, which needs the daemon.
    # With Docker Desktop installed but stopped that is false, and the installer
    # used to run winget, get "already installed / no upgrade available" back as
    # a non-zero exit, and throw DOCKER_INSTALL_FAILED.
    source = setup_source()
    assert "function Test-DockerInstalled" in source
    assert "Get-DockerDesktopPath" in source
    assert "com.docker.service" in source
    assert "Get-Command 'docker.exe'" in source


def test_winget_install_runs_only_when_docker_is_absent():
    source = setup_source()
    body = source[source.index("function Ensure-DockerReady"):]
    body = body[:body.index("\nfunction ")]
    installed_branch = body.index("if (Test-DockerInstalled)")
    winget_install = body.index("winget.exe' @('install'")
    assert installed_branch < winget_install, (
        "the installed-already branch must short-circuit before any install")
    # and the daemon probe comes before both
    assert body.index("Get-DockerServerVersion") < installed_branch


def test_winget_failure_is_rechecked_before_being_fatal():
    source = setup_source()
    body = source[source.index("function Ensure-DockerReady"):]
    body = body[:body.index("\nfunction ")]
    recheck = body.index("if (-not (Test-DockerInstalled)) {")
    fatal = body.index("DOCKER_INSTALL_FAILED")
    assert recheck < fatal, "a non-zero winget exit must be re-detected, not thrown straight away"


def test_docker_is_started_and_awaited():
    source = setup_source()
    assert "function Start-DockerDesktop" in source
    assert "function Wait-DockerDaemon" in source
    assert "Start-Service -Name 'com.docker.service'" in source
    assert "DOCKER_PRESENT_BUT_DAEMON_NOT_RESPONDING" in source


def test_compose_is_reprobed_after_docker_becomes_ready():
    source = setup_source()
    body = source[source.index("function Ensure-Prerequisites"):]
    body = body[:body.index("\nfunction ")]
    # the stale hardware snapshot must no longer decide this
    assert "$Hardware.ComposeAvailable" not in body
    assert body.index("Ensure-DockerReady") < body.index("'compose','version','--short'")


def test_failures_carry_user_facing_advice():
    source = setup_source()
    assert "function Get-FailureAdvice" in source
    for code in ("DOCKER_PRESENT_BUT_DAEMON_NOT_RESPONDING", "DOCKER_INSTALL_FAILED",
                 "DOCKER_INSTALLED_STARTUP_PENDING", "WINGET_REQUIRED",
                 "VIRTUALIZATION_DISABLED_IN_FIRMWARE"):
        assert code in source
    assert "Get-FailureAdvice $_.Exception.Message" in source


def test_logging_cannot_abort_the_installation():
    source = setup_source()
    body = source[source.index("function Write-SafeLog"):]
    body = body[:body.index("\nfunction ")]
    assert "try {" in body and "catch {" in body, (
        "Write-SafeLog is called from catch blocks; it must not raise")


def test_supply_chain_checks_survive_the_docker_changes():
    source = setup_source()
    for code in ("MANIFEST_ORIGIN_NOT_PINNED", "MANIFEST_SIGNATURE_INVALID",
                 "RELEASE_PUBLIC_KEY_NOT_CONFIGURED", "OCI_DIGEST_VERIFICATION_FAILED",
                 "OCI_REFERENCE_NOT_IMMUTABLE", "OCI_PLATFORM_UNEXPECTED"):
        assert code in source


# --- LLM runtime compatibility -------------------------------------------------
#
# vLLM 0.28's default model runner allocates UvaBuffer, which raises
# "UVA is not available" whenever vLLM reports pinned memory unavailable. vLLM
# disables pinned memory under WSL2 by default, and Docker Desktop always runs
# under WSL2, so the engine could not start on the RTX 2060 at all.

def compat_probe_source():
    return (ROOT / "images" / "nova-llm-compat.py").read_text(encoding="utf-8")


def workflow_source():
    return (Path(__file__).parent / ".github" / "workflows"
            / "organization-lab-release.yml").read_text(encoding="utf-8")


def test_the_runtime_mode_is_probed_on_the_real_gpu_before_anything_is_written():
    source = setup_source()
    body = source[source.index("function Invoke-Install"):]
    assert body.index("Resolve-LlmRuntimeMode") < body.index("Write-LabCompose"), (
        "the GPU must decide the runtime before any configuration is generated")
    probe = source[source.index("function Resolve-LlmRuntimeMode"):]
    probe = probe[:probe.index("\nfunction ")]
    # the probe runs the pinned language image against the real card
    assert "'--gpus','all'" in probe
    assert "nova-llm-compat.py" in probe


def test_vllm_is_never_selected_when_no_local_mode_works():
    probe = setup_source()[setup_source().index("function Resolve-LlmRuntimeMode"):]
    probe = probe[:probe.index("\nfunction ")]
    assert "LLM_RUNTIME_INCOMPATIBLE" in probe
    assert "LLM_GPU_NOT_VISIBLE_TO_DOCKER" in probe
    assert "LLM_GPU_TOO_OLD" in probe
    # fail closed: an unrecognised mode is refused rather than guessed at
    assert "LLM_RUNTIME_MODE_UNKNOWN" in probe


def test_the_probe_offers_no_remote_runtime():
    probe = compat_probe_source()
    for forbidden in ("openai.com", "anthropic.com", "api_key", "http://", "https://"):
        assert forbidden not in probe, f"the compatibility probe must stay local: {forbidden}"
    # exactly two local modes, ordered UVA-free first
    assert 'MODES = ("no_uva", "uva_pinned")' in probe


def test_the_uva_mode_is_only_offered_after_a_real_memory_check():
    probe = compat_probe_source()
    body = probe[probe.index("def _probe_uva_pinned"):]
    body = body[:body.index("\nPROBES")]
    # it must actually allocate and read back, not merely consult a flag
    assert "pin_memory=True" in body
    assert "get_accelerator_view_from_cpu_tensor" in body
    assert "did not observe the host write" in body


def test_the_no_uva_mode_is_refused_if_vllm_drops_the_opt_out():
    body = compat_probe_source()
    body = body[body.index("def _probe_no_uva"):]
    body = body[:body.index("\ndef _probe_uva_pinned")]
    assert "VLLM_USE_V2_MODEL_RUNNER" in body
    assert "is not supported by this vLLM build" in body


def test_the_resolved_mode_reaches_the_llm_service():
    source = setup_source()
    compose = source[source.index("function Write-LabCompose"):]
    compose = compose[:compose.index("\n    $path = Join-Path")]
    assert "$llmEnvironment" in compose
    assert "LLM_RUNTIME_NOT_RESOLVED" in compose, (
        "compose must refuse to render without a probed runtime mode")
    assert "VLLM_USE_V2_MODEL_RUNNER" in source
    assert "VLLM_WSL2_ENABLE_PIN_MEMORY" in source


def test_the_language_image_pins_the_verified_vllm_and_ships_the_probe():
    dockerfile = (ROOT / "images" / "llm.Dockerfile").read_text(encoding="utf-8")
    froms = [l.strip() for l in dockerfile.splitlines() if l.startswith("FROM ")]
    assert froms == ["FROM vllm/vllm-openai:v0.28.0"], (
        "the runtime mode decision is only meaningful against a known vLLM")
    assert "/opt/nova/nova-llm-compat.py" in dockerfile


def test_the_speech_model_is_named_by_path_so_the_client_never_calls_hugging_face():
    # nova-server sends NOVA_AI_SPEECH_MODEL as the 'model' form field. A bare
    # name such as 'small' sends faster-whisper to huggingface_hub, which fails
    # offline and would reach Hugging Face from the client machine.
    workflow = workflow_source()
    assert "'model_id': '/models/whisper-small'" in workflow
    assert "'model_id': 'small'" not in workflow
    dockerfile = (ROOT / "images" / "whisper.Dockerfile").read_text(encoding="utf-8")
    assert "WHISPER__MODEL=/models/whisper-small" in dockerfile
    assert "HF_HUB_OFFLINE=1" in dockerfile
    assert "GRADIO_ANALYTICS_ENABLED=0" in dockerfile


def test_llm_failures_carry_user_facing_advice_and_never_offer_the_cloud():
    source = setup_source()
    advice = source[source.index("function Get-FailureAdvice"):]
    advice = advice[:advice.index("\nfunction ")]
    for code in ("LLM_RUNTIME_INCOMPATIBLE", "LLM_GPU_NOT_VISIBLE_TO_DOCKER",
                 "LLM_GPU_TOO_OLD", "LLM_COMPAT_PROBE_FAILED"):
        assert code in advice
    assert "aucun service cloud n est utilise en remplacement" in advice


def test_supply_chain_checks_survive_the_runtime_changes():
    source = setup_source()
    for code in ("MANIFEST_ORIGIN_NOT_PINNED", "MANIFEST_SIGNATURE_INVALID",
                 "OCI_DIGEST_VERIFICATION_FAILED", "OCI_PLATFORM_UNEXPECTED",
                 "DOCKER_PRESENT_BUT_DAEMON_NOT_RESPONDING"):
        assert code in source


def test_the_llm_answers_to_the_name_the_api_asks_for():
    # Without --served-model-name vLLM advertises the on-disk path, while the
    # API sends NOVA_AI_TEXT_MODEL, and every completion returns 404.
    source = setup_source()
    compose = source[source.index("function Write-LabCompose"):]
    compose = compose[:compose.index("\n    $path = Join-Path")]
    served = compose.index('"--served-model-name", "$($Profile.language.model_id)"')
    assert served > 0
    assert 'NOVA_AI_TEXT_MODEL: "$($Profile.language.model_id)"' in compose, (
        "the served name and the name the API asks for must be the same field")


def test_the_database_password_cannot_break_the_connection_url():
    # Convert::ToBase64String's alphabet includes '/', which ends the authority
    # of postgresql://nova:<pw>@postgres:5432/nova. libpq then resolved the host
    # as 'nova' and the install died -- at random, on about half of all runs.
    source = setup_source()
    secret = source[source.index("function Get-OrCreateSecret"):]
    secret = secret[:secret.index("\nfunction ")]
    assert ".Replace('+','-').Replace('/','_')" in secret, "new secrets must be URL-safe"
    # and a password written by an earlier version is encoded at the point of use
    assert source.count("urllib.parse.quote(open(") == 2, (
        "migrate and api must both percent-encode the password")
    assert "postgresql://nova:`$(cat /run/secrets/postgres_password)" not in source, (
        "the raw secret must never be interpolated into the URL")
