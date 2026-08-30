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
