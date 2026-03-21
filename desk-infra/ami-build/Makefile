# SAM Makefile build: vendor desk-sdk into the Lambda artifact (see desk-api Makefile).
build-AmiBuildTasksFunction:
	cp "$(CURDIR)/handler.py" "$(ARTIFACTS_DIR)/"
	mkdir -p "$(ARTIFACTS_DIR)/desk"
	cp -r "$(DESK_SDK_PATH)/src/desk/"* "$(ARTIFACTS_DIR)/desk/"
	pip install -q --target "$(ARTIFACTS_DIR)" "boto3>=1.34" 2>/dev/null || true
