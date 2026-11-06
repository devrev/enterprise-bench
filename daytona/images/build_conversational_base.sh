#!/bin/bash
set -euo pipefail

# Build conversational-base for linux/amd64 and push it to GHCR.
#
# Daytona sandboxes are x86_64, so task images need an amd64 base. This
# cross-builds from an arm64 host via buildx, pushes, then verifies the
# published image (and any baked Claude binary) is amd64 — not arm64.
#
# The build context is images/conversational-base/, extracted from
# artifacts/base-image.zip by `make setup`.
#
# Usage:
#   GHCR_OWNER=<github-user-or-org> ./daytona/images/build_conversational_base.sh
#
# Auth: expects `docker login ghcr.io` to already be done for GHCR_OWNER, e.g.
#   gh auth token | docker login ghcr.io -u "$GHCR_OWNER" --password-stdin

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONTEXT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)/images/conversational-base"

if [[ ! -f "${CONTEXT_DIR}/Dockerfile" ]]; then
    echo "error: ${CONTEXT_DIR}/Dockerfile not found; run \`make setup\` first" >&2
    exit 1
fi

GHCR_OWNER="${GHCR_OWNER:?Set GHCR_OWNER to your GitHub username/org}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
PLATFORM="${PLATFORM:-linux/amd64}"

if [[ "${PLATFORM}" != "linux/amd64" ]]; then
    echo "error: this script publishes the Daytona image; PLATFORM must be linux/amd64 (got ${PLATFORM})" >&2
    exit 1
fi

# GHCR requires a lowercase owner path.
OWNER_LC="$(echo "$GHCR_OWNER" | tr '[:upper:]' '[:lower:]')"
IMAGE="ghcr.io/${OWNER_LC}/conversational-base:${IMAGE_TAG}"

echo "=== Building + pushing conversational base image to GHCR ==="
echo "Image:    ${IMAGE}"
echo "Platform: ${PLATFORM}"
echo "Context:  ${CONTEXT_DIR}"
echo ""

docker buildx build \
    --platform "${PLATFORM}" \
    -t "${IMAGE}" \
    --push \
    "${CONTEXT_DIR}"

echo ""
echo "=== Validating published image is linux/amd64 ==="

# Prefix env applies only to the left of a pipe; export so Python sees IMAGE.
export IMAGE
docker manifest inspect "${IMAGE}" | python3 -c "
import json, os, sys
d = json.load(sys.stdin)
image = os.environ['IMAGE']
manifests = d.get('manifests') or []
if manifests:
    linux_archs = {
        m['platform']['architecture']
        for m in manifests
        if m.get('platform', {}).get('os') == 'linux'
    }
else:
    print('✓ Single-manifest image; architecture checked after pull')
    raise SystemExit(0)
if 'amd64' not in linux_archs:
    sys.exit(f'error: {image} has no linux/amd64 (found {sorted(linux_archs)})')
if 'arm64' in linux_archs:
    sys.exit(f'error: {image} includes linux/arm64; Daytona requires amd64-only')
print('✓ Manifest platform: linux/amd64')
"

docker pull --platform linux/amd64 "${IMAGE}" >/dev/null
pulled_arch="$(docker image inspect --format '{{.Architecture}}' "${IMAGE}")"
if [[ "${pulled_arch}" != "amd64" ]]; then
    echo "error: pulled image architecture is ${pulled_arch}, expected amd64" >&2
    exit 1
fi
echo "✓ Local inspect architecture: amd64"

# Fail if an arm64 Claude package was baked in. If a Claude binary is present,
# it must be an ELF x86-64 file (not arm64).
docker run --rm --platform linux/amd64 --entrypoint python3 "${IMAGE}" -c "
import pathlib, struct, sys

arm64_pkg = pathlib.Path('/usr/lib/node_modules/@anthropic-ai/claude-agent-sdk-linux-arm64')
if arm64_pkg.exists():
    sys.exit('error: image contains claude-agent-sdk-linux-arm64 (not amd64)')

candidates = [
    pathlib.Path('/usr/bin/claude'),
    pathlib.Path('/usr/lib/node_modules/@anthropic-ai/claude-agent-sdk-linux-x64/claude'),
]
found = next((p for p in candidates if p.exists()), None)
if found is None:
    print('✓ No baked Claude binary (Daytona installs linux-x64 at sandbox start)')
    raise SystemExit(0)

data = found.read_bytes()[:20]
if data[:4] != b'\\x7fELF':
    sys.exit(f'error: {found} is not an ELF binary')
machine = struct.unpack_from('<H', data, 18)[0]
# EM_X86_64 = 62
if machine != 62:
    sys.exit(f'error: {found} e_machine={machine} (want 62 / x86-64)')
print(f'✓ Claude binary is amd64/x86-64: {found}')
"

echo ""
echo "✓ Pushed and validated: ${IMAGE}"
echo ""
echo "Next steps for a Daytona run:"
echo "  1. Make the package public (or configure Daytona registry auth):"
echo "     https://github.com/users/${OWNER_LC}/packages/container/conversational-base/settings"
echo "  2. Task Dockerfiles may use FROM enterprise-bench/conversational-base:latest;"
echo "     DaytonaEnvironment remaps that to GHCR when building on Daytona."
echo "  3. harbor run -e environments.daytona_env:DaytonaEnvironment ..."