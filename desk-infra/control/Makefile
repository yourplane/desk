# Install desk-sdk (and boto3) into Lambda artifact dir. No desk/ copy; build.sh runs from desk-infra/.
build-DeskControlFunction:
	@if ! command -v uv >/dev/null 2>&1; then echo "Error: uv required. Install with: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; fi; \
	uv pip install --quiet --target "$(ARTIFACTS_DIR)" "$(CURDIR)/../../desk-sdk"; \
	cp "$(CURDIR)/handler.py" "$(ARTIFACTS_DIR)/"
