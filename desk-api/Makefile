# SAM build: copy app, lambda_handler, desk-sdk and install deps into ARTIFACTS_DIR.
# SAM copies CodeUri to a temp dir, so desk-sdk path must come from env (set by deploy script).
build-DeskApiFunction:
	cp -r "$(CURDIR)/app" "$(ARTIFACTS_DIR)/"
	cp "$(CURDIR)/lambda_handler.py" "$(ARTIFACTS_DIR)/"
	mkdir -p "$(ARTIFACTS_DIR)/desk"
	cp -r "$(DESK_SDK_PATH)/src/desk/"* "$(ARTIFACTS_DIR)/desk/"
	pip install -q --target "$(ARTIFACTS_DIR)" "fastapi>=0.100" "mangum>=0.17" "boto3>=1.34" 2>/dev/null || true
