# Requires desk/ to exist (run ../build.sh or copy ../../src/desk into reaper/desk first)
build-ReaperFunction:
	@if [ ! -d "$(CURDIR)/desk" ]; then \
		echo "Error: reaper/desk not found. Run from repo root: infrastructure/build.sh"; exit 1; \
	fi; \
	if [ ! -d "$(CURDIR)/.venv" ]; then \
		python3 -m venv "$(CURDIR)/.venv"; \
	fi; \
	"$(CURDIR)/.venv/bin/pip" install --quiet --upgrade pip; \
	"$(CURDIR)/.venv/bin/pip" install --quiet -r "$(CURDIR)/requirements.txt" --target "$(ARTIFACTS_DIR)"; \
	cp -r "$(CURDIR)/desk" "$(ARTIFACTS_DIR)/"; \
	cp "$(CURDIR)/handler.py" "$(ARTIFACTS_DIR)/"
