"""Verify that the registry really serves the bytes the signed Nova manifest pins.

For every OCI artifact in the manifest this:

  1. obtains an anonymous pull token, using no credentials at all;
  2. downloads the image manifest addressed by the pinned digest;
  3. recomputes sha256 over the received bytes and compares it to that digest;
  4. downloads the config blob and every layer, recomputing each digest;
  5. requires the three Nova images to be present and to pass.

It is deliberately a client-side check: it speaks plain HTTP to the registry the
way the installer does, and shares no code with the workflow that produced the
manifest, so a mistake in the release pipeline cannot mask itself here.

Usage:
    python verify-release-images.py <manifest.json | https://...> [--skip-layers]
"""

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request

ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])

REQUIRED_NOVA_IMAGES = {
    "ghcr.io/mailpro31/nova-server",
    "ghcr.io/mailpro31/nova-quick-lab-speech",
    "ghcr.io/mailpro31/nova-quick-lab-language",
}

CHUNK = 1024 * 1024


class VerificationError(Exception):
    pass


def open_url(url, token=None, accept=None):
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", "Bearer " + token)
    if accept:
        request.add_header("Accept", accept)
    return urllib.request.urlopen(request, timeout=300)


def read_url(url, token=None, accept=None):
    with open_url(url, token, accept) as response:
        return response.read()


def registry_endpoints(registry):
    if registry == "ghcr.io":
        return "https://ghcr.io", "https://ghcr.io/token?scope=repository:{repo}:pull&service=ghcr.io"
    if registry == "docker.io":
        return ("https://registry-1.docker.io",
                "https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull")
    raise VerificationError(f"registry not approved for distribution: {registry}")


def anonymous_token(registry, repo):
    _, token_url = registry_endpoints(registry)
    return json.loads(read_url(token_url.format(repo=repo)))["token"]


def digest_of_stream(stream):
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = stream.read(CHUNK)
        if not chunk:
            break
        digest.update(chunk)
        total += len(chunk)
    return "sha256:" + digest.hexdigest(), total


def verify_artifact(artifact, skip_layers):
    reference = artifact["image"]
    repo_part, pinned = reference.split("@")
    registry, repo = repo_part.split("/", 1)
    host, _ = registry_endpoints(registry)
    token = anonymous_token(registry, repo)

    # (2) and (3): the manifest, and its digest recomputed from the bytes served.
    raw = read_url(f"{host}/v2/{repo}/manifests/{pinned}", token, ACCEPT)
    actual = "sha256:" + hashlib.sha256(raw).hexdigest()
    if actual != pinned:
        raise VerificationError(
            f"{artifact['id']}: manifest bytes hash to {actual}, manifest pins {pinned}")

    document = json.loads(raw)
    if "manifests" in document:
        raise VerificationError(
            f"{artifact['id']}: {pinned} is an image index, not a platform manifest. "
            "The client would have to choose a platform itself, so the signed "
            "manifest would not name the bytes that end up on disk.")

    config_digest = document["config"]["digest"]
    blob = read_url(f"{host}/v2/{repo}/blobs/{config_digest}", token)
    actual_config = "sha256:" + hashlib.sha256(blob).hexdigest()
    if actual_config != config_digest:
        raise VerificationError(
            f"{artifact['id']}: config blob hashes to {actual_config}, manifest says {config_digest}")

    config = json.loads(blob)
    platform = f"{config.get('os')}/{config.get('architecture')}"
    declared = artifact.get("platform")
    if declared and platform != declared:
        raise VerificationError(
            f"{artifact['id']}: image is {platform}, manifest declares {declared}")

    layers = document.get("layers", [])
    checked_bytes = 0
    if not skip_layers:
        for index, layer in enumerate(layers, start=1):
            with open_url(f"{host}/v2/{repo}/blobs/{layer['digest']}", token) as stream:
                actual_layer, size = digest_of_stream(stream)
            if actual_layer != layer["digest"]:
                raise VerificationError(
                    f"{artifact['id']}: layer {index} hashes to {actual_layer}, "
                    f"manifest says {layer['digest']}")
            checked_bytes += size

    return {
        "id": artifact["id"],
        "repo": repo_part,
        "digest": pinned,
        "platform": platform,
        "layers": len(layers),
        "bytes": checked_bytes,
    }


def load_manifest(source):
    if source.startswith("https://"):
        return json.loads(read_url(source))
    return json.loads(open(source, encoding="utf-8").read())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", help="path or https URL of the signed Nova manifest")
    parser.add_argument("--skip-layers", action="store_true",
                        help="verify manifests and configs only, not layer blobs")
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    artifacts = [a for a in manifest["artifacts"] if a.get("kind") == "oci_image"]
    if not artifacts:
        print("no OCI artifacts in the manifest", file=sys.stderr)
        return 1

    print(f"verifying {len(artifacts)} images from {manifest.get('release_id')}, "
          f"anonymously, layers {'skipped' if args.skip_layers else 'included'}\n")

    results, failures = [], []
    for artifact in artifacts:
        try:
            result = verify_artifact(artifact, args.skip_layers)
        except (VerificationError, urllib.error.HTTPError, urllib.error.URLError, KeyError) as error:
            failures.append(f"{artifact.get('id')}: {error}")
            print(f"  FAIL  {artifact.get('id')}: {error}")
        else:
            results.append(result)
            print(f"  ok    {result['id']:<14} {result['platform']:<12} "
                  f"{result['layers']:>2} layers  {result['bytes'] / 1e9:>6.2f} GB verified")

    # (5) the three Nova images must all be present and verified.
    verified_repos = {r["repo"] for r in results}
    missing = REQUIRED_NOVA_IMAGES - verified_repos
    if missing:
        failures.append("required Nova images not verified: " + ", ".join(sorted(missing)))

    print()
    if failures:
        for failure in failures:
            print(f"FAILED: {failure}", file=sys.stderr)
        return 1

    total = sum(r["bytes"] for r in results)
    print(f"all {len(results)} images verified against the signed manifest "
          f"({total / 1e9:.2f} GB of content re-hashed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
