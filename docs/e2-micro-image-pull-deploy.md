# E2-micro image pull deploy

Build the app image on GitHub Actions and pull it on the VM. This avoids running dependency-heavy Docker builds on the free-tier host.

The image workflow publishes the main tag and the commit SHA tag with Buildx cache enabled. The VM deploy script writes a compose env file, ensures the VM compose file exists, pulls the selected image, runs the smoke test, and installs cron.

By default the deploy script pulls the main tag for this repository. Override it by setting POMA_IMAGE, or override the default registry, repository, and tag pieces with DEFAULT_IMAGE_REGISTRY, DEFAULT_IMAGE_REPOSITORY, and DEFAULT_IMAGE_TAG.

The VM-side image pull is bounded at 8 minutes, and the deploy smoke test is bounded at 3 minutes. The deploy script runs timed Compose calls through `timeout_compose`, which invokes `docker compose` directly so GNU `timeout` does not try to execute a shell function named `compose`. Local development can keep using docker-compose.yml with build enabled; the VM uses docker-compose.vm.yml with an image reference. When an image is built instead of pulled, pass the host runtime identity through Docker build args (`APP_UID=${POMA_UID}` and `APP_GID=${POMA_GID}`) so the container user matches the deployed runtime directories.
