import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


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
    assert body.index("Resolve-LlmRuntimeMode") < body.index("Write-StackCompose"), (
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
    compose = source[source.index("function Write-StackCompose"):]
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
    compose = source[source.index("function Write-StackCompose"):]
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


# --- compose output interpretation ---------------------------------------------
#
# `COMPOSE_FAILED: Container nova-organization-lab-postgres-1 Running` on a
# container that was genuinely running, and the same for Whisper before it.
# $ErrorActionPreference is 'Stop' script-wide, and under 'Stop' a `2>&1`
# redirection on a native command turns the FIRST stderr line into a terminating
# NativeCommandError. Run-Captured's catch then returned exit code 127 and that
# line as the message, so the real exit code was never read -- and docker compose
# writes all of its progress to stderr.

def test_stderr_from_a_successful_command_is_not_a_failure():
    body = setup_source()
    body = body[body.index("function Run-Captured"):]
    body = body[:body.index("\nfunction ")]
    assert "$ErrorActionPreference = 'Continue'" in body, (
        "under 'Stop', the first stderr line aborts the call before the exit "
        "code can be read")
    assert "$code = $LASTEXITCODE" in body
    # the old shape must be gone
    assert "$text = & $File @Arguments 2>&1 | Out-String" not in body


def test_compose_success_is_decided_by_the_exit_code_alone():
    body = setup_source()
    body = body[body.index("function Invoke-Compose"):]
    body = body[:body.index("\nfunction ")]
    assert "if ($result.ExitCode -ne 0)" in body
    # nothing may test the text of compose's output to decide success
    for forbidden in ("Running", "-match", "Select-String"):
        assert forbidden not in body, (
            "compose progress text must never decide success: " + forbidden)


def test_running_and_healthy_are_never_failures():
    # postgres and redis declare healthchecks; whisper, llm and api do not.
    # Both shapes must count as up.
    body = setup_source()
    body = body[body.index("function ConvertTo-ServiceState"):]
    body = body[:body.index("\nfunction ")]
    assert "'healthy'   { 'healthy' }" in body
    assert "'starting'  { 'starting' }" in body
    assert "'unhealthy' { 'unhealthy' }" in body
    # a running container with no healthcheck -- whisper, the first false failure
    assert "default     { 'running' }" in body
    # a container that exited 0 -- migrate, which is meant to end
    assert "'exited'     { if ($exit -eq 0) { 'completed' } else { 'failed' } }" in body


def test_the_state_comes_from_docker_inspect():
    body = setup_source()
    body = body[body.index("function Get-ServiceState"):]
    body = body[:body.index("\nfunction ")]
    assert "'inspect','--format'" in body
    assert "{{.State.Status}}" in body
    assert "{{.State.ExitCode}}" in body
    assert ".State.Health" in body


def test_the_installer_waits_for_services_before_asking_the_api():
    body = setup_source()
    install = body[body.index("function Invoke-Install"):]
    assert install.index("Invoke-Compose @('up','-d')") < install.index("Wait-ComposeServices"), (
        "the wait belongs after the up")
    assert install.index("Wait-ComposeServices") < install.index("Get-Health"), (
        "container state is checked before the HTTP health probe")


def test_logs_are_read_only_for_a_container_that_actually_broke():
    body = setup_source()
    body = body[body.index("function Wait-ComposeServices"):]
    body = body[:body.index("\nfunction ")]
    logs = body.index("'logs','--tail'")
    guard = body.index("if ($broken.Count -gt 0)")
    assert guard < logs, "logs must be collected only inside the failure branch"
    # starting is patience, not failure
    assert "'starting'  { $pending += $name }" in body
    assert "SERVICE_START_TIMEOUT" in body


def test_service_failures_carry_advice():
    body = setup_source()
    advice = body[body.index("function Get-FailureAdvice"):]
    advice = advice[:advice.index("\nfunction ")]
    for code in ("SERVICE_FAILED", "SERVICE_START_TIMEOUT",
                 "COMPOSE_NO_CONTAINERS", "COMPOSE_FAILED"):
        assert code in advice


def test_supply_chain_and_runtime_checks_survive_the_compose_changes():
    body = setup_source()
    for code in ("MANIFEST_ORIGIN_NOT_PINNED", "MANIFEST_SIGNATURE_INVALID",
                 "OCI_DIGEST_VERIFICATION_FAILED", "OCI_PLATFORM_UNEXPECTED",
                 "LLM_RUNTIME_INCOMPATIBLE", "DOCKER_PRESENT_BUT_DAEMON_NOT_RESPONDING"):
        assert code in body


def test_the_stack_and_the_gateway_composers_do_not_share_a_name():
    # Deux fonctions du meme nom : PowerShell garde la derniere definie. La
    # pile de production etait ecrite par la fonction de la passerelle, avec
    # trois arguments pour deux parametres.
    body = setup_source()
    assert body.count("function Write-StackCompose") == 1
    assert body.count("function Write-LabGatewayCompose") == 1
    assert "function Write-LabCompose" not in body


def test_the_installer_writes_the_stack_compose_with_its_three_arguments():
    body = setup_source()
    assert re.search(
        r"\$compose = Write-StackCompose \$manifest \$releaseProfile \$runtime", body
    )
    assert re.search(r"Write-LabGatewayCompose \$manifest \$settings", body)


def test_a_regeneration_reuses_the_existing_organization_and_deployment():
    # Une reparation ou une mise a jour ne doit pas rendre invisible la base
    # deja indexee par l'identifiant d'organisation.
    body = setup_source()
    assert "function Get-GeneratedIdentity" in body
    assert '$identity = Get-GeneratedIdentity' in body
    assert re.search(
        r"\$orgId = if \(\$identity\.OrganizationId\) \{ \$identity\.OrganizationId \}"
        r" else \{ 'org-' \+", body)
    assert re.search(
        r"\$deploymentId = if \(\$identity\.DeploymentId\) \{ \$identity\.DeploymentId \}"
        r" else \{ 'dpl-' \+", body)


def test_the_identity_is_read_from_the_generated_compose_not_invented():
    body = setup_source()
    identity = body[body.index("function Get-GeneratedIdentity"):]
    identity = identity[:identity.index("\nfunction ")]
    assert "compose.quick-lab.yml" in identity
    assert "NOVA_ORGANIZATION_ID" in identity
    assert "deployment_id" in identity
    # Aucune ecriture : cette fonction lit, elle ne repare pas.
    assert "WriteAllText" not in identity
    assert "Set-Content" not in identity


def test_the_generator_never_hardcodes_a_server_image_tag():
    # L'identite d'image vient du manifeste signe, jamais d'une constante du
    # script : c'est ce qui rend une mise a jour verifiable.
    body = setup_source()
    assert "nova-local/server:" not in body
    assert "image: $($Manifest.images.server)" in body


def test_the_release_manifest_versions_the_local_tag_and_pins_the_digest():
    body = workflow_source()
    assert "f'nova-local/server:{version}'" in body
    assert "ghcr.io/mailpro31/nova-server@{os.environ[\"SERVER_DIGEST\"]}" in body
    assert "'images': {'server': f'nova-local/server:{version}'" in body


def test_the_regeneration_test_is_shipped_and_offline():
    # Le test de regeneration doit rester executable sans Docker, sans reseau
    # et sans toucher au ProgramData reel : c'est ce qui permet de le lancer
    # sur un poste en service.
    script = (ROOT / "test-regeneration.ps1").read_text(encoding="utf-8")
    assert "-LibraryMode" in script
    assert "Write-StackCompose" in script
    assert "GetTempPath" in script
    for forbidden in ("docker", "Invoke-WebRequest", "Invoke-Compose"):
        assert forbidden not in script


def test_a_regeneration_can_be_verified_on_this_machine():
    # Execute pour de vrai quand PowerShell est disponible ; ignore ailleurs,
    # le generateur etant un script Windows.
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell absent")
    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(ROOT / "test-regeneration.ps1")],
        capture_output=True, text=True, timeout=300,
    )
    assert "REGENERATION_TEST_OK" in completed.stdout, completed.stdout + completed.stderr
    assert completed.returncode == 0


def test_the_database_secret_restricts_its_access_for_real():
    # Deux erreurs cumulees rendaient la restriction inoperante : `$env:USERNAME:`
    # se lit comme une variable qualifiee par un lecteur, et `/grant:r:` n'est
    # pas la syntaxe d'icacls. L'echec partait dans Out-Null.
    body = setup_source()
    secret = body[body.index("function Get-OrCreateSecret"):]
    secret = secret[:secret.index("\nfunction ")]
    # Le commentaire cite les deux formes fautives : seul le code compte.
    secret = "\n".join(
        line for line in secret.splitlines() if not line.strip().startswith("#")
    )
    assert "$env:USERNAME" not in secret
    assert "/grant:r:" not in secret
    assert "'/grant:r'" in secret
    assert "WindowsIdentity]::GetCurrent()).User.Value" in secret
    assert "icacls.exe" in secret and "Out-Null" not in secret


def test_a_secret_that_could_not_be_protected_is_not_kept():
    body = setup_source()
    secret = body[body.index("function Get-OrCreateSecret"):]
    secret = secret[:secret.index("\nfunction ")]
    assert "$acl.ExitCode -ne 0" in secret
    assert "Remove-Item" in secret
    assert "throw 'SECRET_ACL_NOT_APPLIED'" in secret
    assert "SECRET_ACL_NOT_APPLIED" in body[body.index("function Get-FailureAdvice"):]


def test_the_regeneration_test_checks_the_secret_acl():
    script = (ROOT / "test-regeneration.ps1").read_text(encoding="utf-8")
    assert "AreAccessRulesProtected" in script
    # S-1-1-0 Everyone, S-1-5-32-545 Users : ni l'un ni l'autre ne doit
    # figurer dans les droits du fichier.
    assert "S-1-1-0" in script and "S-1-5-32-545" in script
    assert "SECRET_OWNER_NOT_GRANTED" in script


# ─────────────────────── Le premier administrateur ────────────────────────
#
# Une installation qui va jusqu'au bout sans nommer personne livre un serveur
# que personne ne peut piloter : la pile repond, /api/readyz dit oui, et Nova
# Admin refuse tout le monde. Ces tests decrivent ce que l'installateur doit
# faire pour que cela n'arrive plus.


def first_admin_body() -> str:
    body = setup_source()
    start = body.index("function Set-FirstAdministrators")
    return body[start:body.index("\nfunction ", start)]


def test_the_installer_names_a_first_administrator():
    body = setup_source()
    for expected in ("function Resolve-InstallAnswers",
                     "function Set-FirstAdministrators",
                     "function Read-InstallAnswers",
                     "$answers = Resolve-InstallAnswers",
                     "$administrators = @($answers.administrators)",
                     "$promoted = Set-FirstAdministrators $administrators"):
        assert expected in body


def test_the_promotion_runs_inside_the_api_container():
    body = setup_source()
    invoke = body[body.index("function Invoke-AdminCli"):]
    invoke = invoke[:invoke.index("\nfunction ")]
    assert "'exec','-T','api'" in invoke
    assert "'python','admin_cli.py'" in invoke
    assert "grant" in first_admin_body()
    assert "'--create-if-missing'" in first_admin_body()


def test_the_operator_tool_is_actually_in_the_image():
    # Le geste decrit par le runbook echouait sur `can't open file` : le
    # Dockerfile listait ses fichiers un par un et ne contenait pas celui-la.
    dockerfile = (Path(__file__).parent / "Dockerfile").read_text(encoding="utf-8")
    assert "admin_cli.py" in dockerfile


def test_a_missing_address_stops_a_silent_install_before_any_work():
    body = setup_source()
    install = body[body.index("function Invoke-Install"):]
    install = install[:install.index("\n}\n")]
    # Premiere instruction executable du corps : rien n'est telecharge, aucun
    # materiel n'est sonde avant de savoir qui administrera le serveur.
    statements = [line.strip() for line in install.splitlines()[1:]
                  if line.strip() and not line.strip().startswith("#")]
    assert statements[0] == "$answers = Resolve-InstallAnswers"
    resolve = body[body.index("function Resolve-InstallAnswers"):]
    resolve = resolve[:resolve.index("\nfunction ")]
    assert "if ($Headless) { throw 'FIRST_ADMIN_EMAIL_REQUIRED' }" in resolve


def test_the_address_is_validated_before_the_container_is_reached():
    body = first_admin_body()
    # La verification precede la boucle qui appelle le conteneur : une saisie
    # fautive ne doit pas creer un compte a moitie.
    assert body.index("FIRST_ADMIN_EMAIL_INVALID") < body.index("Invoke-AdminCli")
    assert "[ValidatePattern(" in setup_source()


def test_the_second_administrator_is_offered_but_never_imposed():
    body = setup_source()
    assert "[string]$SecondAdminEmail = ''" in body
    report = body[body.index("function Get-AdministratorReport"):]
    report = report[:report.index("\nfunction ")]
    # Un administrateur unique doit se voir, avec la commande exacte a jouer.
    assert "$Count -lt 2" in report
    assert "grant <adresse> organization_admin --create-if-missing" in report


def test_a_failed_promotion_does_not_roll_the_stack_back():
    # Restore-PreviousConfiguration ramenerait la pile precedente. Une
    # promotion ratee n'est pas une raison d'annuler une mise a jour reussie.
    body = setup_source()
    install = body[body.index("function Invoke-Install"):]
    install = install[:install.index("\n}\n")]
    assert install.index("Restore-PreviousConfiguration") < install.index(
        "Set-FirstAdministrators")
    rollback = install[install.index("} catch {"):]
    assert "Set-FirstAdministrators" not in rollback.split("Save-State 'ready'")[0]


def test_a_failed_promotion_is_visible_and_carries_its_instructions():
    body = setup_source()
    advice = body[body.index("function Get-FailureAdvice"):]
    for code in ("FIRST_ADMIN_EMAIL_REQUIRED", "FIRST_ADMIN_EMAIL_INVALID",
                 "FIRST_ADMIN_PROMOTION_FAILED", "ADMIN_EMAIL_ACL_NOT_APPLIED"):
        assert code in advice
    assert "admin_cli.py grant <adresse> organization_admin" in advice


def test_the_address_never_reaches_the_log():
    # Write-SafeLog ne masque que password|token|secret|credential : une adresse
    # y passerait en clair. Le message d'echec est journalise tel quel par le
    # bloc d'erreur final, il ne doit donc porter que le code de sortie.
    body = first_admin_body()
    assert "throw ('FIRST_ADMIN_PROMOTION_FAILED: exit {0}' -f $result.ExitCode)" in body
    assert "$result.Output" not in body
    assert "Write-SafeLog ('first_admin promoted count={0}' -f $Emails.Count)" in body


def test_the_retained_address_is_protected_like_a_secret():
    body = setup_source()
    protect = body[body.index("function Protect-OperatorFile"):]
    protect = protect[:protect.index("\nfunction ")]
    assert "$env:USERNAME" not in protect
    assert "'/grant:r'" in protect
    assert "WindowsIdentity]::GetCurrent()).User.Value" in protect
    assert "$acl.ExitCode -ne 0" in protect
    assert "Remove-Item" in protect
    assert "throw 'ADMIN_EMAIL_ACL_NOT_APPLIED'" in protect


def test_a_resume_after_the_reboot_still_knows_who_to_promote():
    # Register-Resume ecrit `"<exe>" resume`, sans argument : sans relecture,
    # la reprise apres le redemarrage WSL2 ne nommerait personne.
    body = setup_source()
    resume = body[body.index("function Register-Resume"):]
    resume = resume[:resume.index("\nfunction ")]
    assert "$FirstAdminEmail" not in resume
    assert "Save-InstallAnswers $answers" in body
    stored = body[body.index("function Read-InstallAnswers"):]
    stored = stored[:stored.index("\nfunction ")]
    assert "Get-InstallAnswersPath" in stored


def test_the_selftest_proves_the_address_check():
    body = setup_source()
    selftest = body[body.index("if ($Action -eq 'selftest')"):]
    assert "ADMIN_EMAIL_SHAPE_OK" in selftest
    assert "ADMIN_ADVICE_OK" in selftest
    assert "Test-EmailShape" in selftest


def test_supply_chain_checks_survive_the_first_admin_changes():
    body = setup_source()
    for code in ("MANIFEST_ORIGIN_NOT_PINNED", "MANIFEST_SIGNATURE_INVALID",
                 "OCI_REFERENCE_NOT_IMMUTABLE", "OCI_DIGEST_VERIFICATION_FAILED",
                 "OCI_PLATFORM_UNEXPECTED", "RELEASE_PUBLIC_KEY_NOT_CONFIGURED"):
        assert code in body
    assert "$script:AllowedManifestOrigin = 'https://releases.novaspeak.app'" in body


def test_the_first_admin_test_is_shipped_and_offline():
    script = (ROOT / "test-first-admin.ps1").read_text(encoding="utf-8")
    assert "-LibraryMode" in script
    assert "GetTempPath" in script
    # Le double remplace l'appel reel : rien n'est demarre.
    assert "function Run-Captured" in script
    for forbidden in ("Invoke-WebRequest", "Invoke-Compose", "Invoke-RestMethod"):
        assert forbidden not in script


def test_the_first_admin_test_covers_the_four_situations():
    script = (ROOT / "test-first-admin.ps1").read_text(encoding="utf-8")
    for marker in ("MALFORMED_EMAIL_REACHED_CONTAINER", "PROMOTION_CALL_COUNT_WRONG",
                   "REPLAY_IS_NOT_IDENTICAL", "PROMOTION_FAILURE_SWALLOWED",
                   "PROMOTION_FAILURE_LEAKS_THE_ADDRESS", "ADDRESS_IN_THE_LOG",
                   "AreAccessRulesProtected"):
        assert marker in script


def test_the_first_admin_path_can_be_verified_on_this_machine():
    # Execute pour de vrai quand PowerShell est disponible ; ignore ailleurs,
    # l'installateur etant un script Windows.
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell absent")
    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(ROOT / "test-first-admin.ps1")],
        capture_output=True, text=True, timeout=300,
    )
    assert "FIRST_ADMIN_TEST_OK" in completed.stdout, completed.stdout + completed.stderr
    assert completed.returncode == 0


# ──────────────────── Les domaines de l'organisation ──────────────────────
#
# main.py lit NOVA_DOMAINS et retombe, a defaut, sur les domaines d'un
# deploiement mono-tenant anterieur. L'installeur ne posait jamais la variable :
# le conteneur servait donc ce defaut, et tout membre autre que l'administrateur
# echouait a sa premiere connexion. Une installation pouvait paraitre reussie en
# ne testant que l'administrateur.


def answers_body() -> str:
    body = setup_source()
    start = body.index("function Resolve-InstallAnswers")
    return body[start:body.index("\nfunction ", start)]


def test_the_generated_compose_declares_the_organization_domains():
    # Le test qui echouait avant ce correctif : la variable n'existait nulle
    # part dans le compose genere.
    body = setup_source()
    assert 'NOVA_DOMAINS: "$script:ResolvedDomains"' in body
    assert "[string]$OrganizationDomains = ''" in body


def test_no_compose_can_be_written_without_domains():
    # La garantie est structurelle : elle vit dans le generateur, pas dans un
    # controle en amont qu'un futur chemin d'appel pourrait contourner.
    body = setup_source()
    composer = body[body.index("function Write-StackCompose"):]
    composer = composer[:composer.index("\nfunction ")]
    assert "throw 'ORGANIZATION_DOMAINS_REQUIRED'" in composer
    assert "throw 'ORGANIZATION_DOMAINS_INVALID'" in composer
    # Avant la creation du secret : un refus ne doit rien laisser derriere lui.
    assert composer.index("ORGANIZATION_DOMAINS_REQUIRED") < composer.index("$dbSecret = Get-OrCreateSecret")


def test_the_server_default_is_never_relied_upon():
    # Aucun domaine en dur, et surtout pas ceux du defaut herite.
    body = setup_source()
    for inherited in ("ipsa.fr", "eleves.ipsa.fr"):
        assert inherited not in body
    # Le parametre n'a pas de valeur par defaut : l'absence est une erreur, pas
    # un repli silencieux.
    assert "[string]$OrganizationDomains = ''" in body
    assert "$OrganizationDomains = 'exemple" not in body


def test_a_silent_install_without_domains_stops_before_any_work():
    body = setup_source()
    install = body[body.index("function Invoke-Install"):]
    install = install[:install.index("\n}\n")]
    statements = [line.strip() for line in install.splitlines()[1:]
                  if line.strip() and not line.strip().startswith("#")]
    assert statements[0] == "$answers = Resolve-InstallAnswers"
    assert "if ($Headless) { throw 'ORGANIZATION_DOMAINS_REQUIRED' }" in answers_body()


def test_the_domain_list_is_validated_before_anything_is_written():
    body = setup_source()
    assert "function Test-DomainListShape" in body
    assert "function ConvertTo-DomainList" in body
    # Le parametre est valide a la liaison, comme les adresses.
    parameters = body[:body.index("Set-StrictMode")]
    assert parameters.count("[ValidatePattern(") == 3


def test_an_administrator_outside_the_declared_domains_is_refused():
    # Il serait promu et ne pourrait jamais se connecter : autant le dire tout
    # de suite plutot que devant l'ecran de connexion.
    assert "throw 'FIRST_ADMIN_EMAIL_OUTSIDE_DOMAINS'" in answers_body()
    advice = setup_source()[setup_source().index("function Get-FailureAdvice"):]
    for code in ("ORGANIZATION_DOMAINS_REQUIRED", "ORGANIZATION_DOMAINS_INVALID",
                 "FIRST_ADMIN_EMAIL_OUTSIDE_DOMAINS"):
        assert code in advice


def test_the_answers_survive_the_reboot_that_resume_needs():
    # RunOnce relance `resume` sans argument : sans ce fichier, la reprise
    # regenererait le compose avec les valeurs par defaut, donc sans domaines.
    body = setup_source()
    for expected in ("function Read-InstallAnswers", "function Save-InstallAnswers",
                     "$answers = Resolve-InstallAnswers", "Save-InstallAnswers $answers",
                     "$script:ResolvedDomains = $answers.domains"):
        assert expected in body
    resume = body[body.index("function Register-Resume"):]
    resume = resume[:resume.index("\nfunction ")]
    assert "$OrganizationDomains" not in resume


def test_the_answers_file_is_protected_like_a_secret():
    body = setup_source()
    save = body[body.index("function Save-InstallAnswers"):]
    save = save[:save.index("\nfunction ")]
    assert "Protect-OperatorFile $path" in save


def test_the_organization_name_is_no_longer_lost_on_a_resume():
    # Meme defaut que les domaines, et meme remede : sans memoire, une reprise
    # regenerait le compose avec « Nova Lab ».
    body = setup_source()
    assert "$script:OrganizationName = $answers.organization_name" in body
    assert "$script:OrganizationType = $answers.organization_type" in body
    # $PSBoundParameters est propre a chaque portee : celui d'une fonction ne
    # dit rien des arguments du script, d'ou la capture au niveau du script.
    assert "$script:BoundParameters = $PSBoundParameters" in body
    assert "$script:BoundParameters.ContainsKey('OrganizationName')" in answers_body()


def test_the_launcher_forwards_every_argument():
    # Seul args[0] atteignait le script : -FirstAdminEmail et
    # -OrganizationDomains etaient jetes en silence, et l'installation
    # retombait sur des valeurs que personne n'avait choisies.
    launcher = (ROOT / "installer" / "NovaOrganizationServerSetup.cs").read_text(encoding="utf-8")
    assert "for (int index = firstForwarded; index < args.Length; index++)" in launcher
    assert "arguments.Append(' ').Append(Quote(args[index]))" in launcher
    # Un premier jeton commencant par '-' est un parametre, pas une action.
    assert 'bool hasAction = args.Length > 0 && !args[0].StartsWith("-");' in launcher
    # Un nom de parametre ne doit jamais etre entoure de guillemets : il
    # deviendrait un argument positionnel.
    quote = launcher[launcher.index("private static string Quote"):]
    quote = quote[:quote.index("\n    private static bool")]
    assert "IndexOfAny" in quote and "return value;" in quote


def test_the_selftest_proves_the_domain_rules():
    body = setup_source()
    selftest = body[body.index("if ($Action -eq 'selftest')"):]
    for marker in ("ORGANIZATION_DOMAIN_SHAPE_OK", "ORGANIZATION_DOMAIN_GUARD_OK",
                   "ORGANIZATION_DOMAIN_ADVICE_OK", "COMPOSE_WRITTEN_WITHOUT_DOMAINS"):
        assert marker in selftest


def test_the_regeneration_test_checks_the_domains():
    script = (ROOT / "test-regeneration.ps1").read_text(encoding="utf-8")
    assert "$script:ResolvedDomains" in script
    assert "DOMAINS_NOT_IN_COMPOSE" in script
    assert "COMPOSE_WRITTEN_WITHOUT_DOMAINS" in script


def test_supply_chain_checks_survive_the_domain_changes():
    body = setup_source()
    for code in ("MANIFEST_ORIGIN_NOT_PINNED", "MANIFEST_SIGNATURE_INVALID",
                 "OCI_REFERENCE_NOT_IMMUTABLE", "OCI_DIGEST_VERIFICATION_FAILED",
                 "OCI_PLATFORM_UNEXPECTED", "RELEASE_PUBLIC_KEY_NOT_CONFIGURED"):
        assert code in body
    assert "$script:AllowedManifestOrigin = 'https://releases.novaspeak.app'" in body


def test_a_rollback_does_not_leave_the_state_announcing_the_failed_release():
    """L'etat doit suivre le compose, pas l'intention.

    Le 5 septembre 2026, une installation de la release 2026.09.05.1 a echoue
    au demarrage des conteneurs. Le retour arriere a bien remis le compose
    precedent -- date du 31 aout, epinglant une image plus ancienne -- mais
    `setup-state.json` a continue d'annoncer la release qui venait d'echouer.
    L'exploitant croyait faire tourner une version et en faisait tourner une
    autre, et l'ecart n'apparaissait nulle part.
    """
    body = setup_source()
    body = body[body.index("function Restore-PreviousConfiguration"):]
    body = body[:body.index("\nfunction ")]
    assert "Save-State" in body, (
        "un retour arriere qui ne corrige pas l'etat laisse une installation "
        "qui ment sur sa propre version")
    assert "rolled-back" in body, "l'etat doit nommer ce qui s'est passe"
    # La release annoncee doit venir du compose restaure, jamais du manifeste
    # que l'on tentait de deployer.
    assert "Get-ComposeStamp $previous" in body, (
        "la release annoncee doit etre lue dans le compose effectivement restaure")


def test_the_compose_stamp_is_read_from_the_file_it_describes():
    body = setup_source()
    body = body[body.index("function Get-ComposeStamp"):]
    body = body[:body.index("\nfunction ")]
    for champ in ("release_id", "profile_id"):
        assert champ in body, f"le tampon doit porter {champ}"
