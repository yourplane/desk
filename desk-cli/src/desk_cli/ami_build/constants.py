"""S3 keys and prefixes for async AMI builds."""

AMI_BUILDS_PREFIX = "ami-builds/"
AMI_BUILD_ARCHIVE_PREFIX = "ami-build-archive/"
# Written by `desk ami build step` after the builder instance is launched.
BUILDER_INSTANCE_KEY = "builder-instance.json"
# Post-recipe AMI registration progress (image id, completion flag).
AMI_RESULT_KEY = "ami-result.json"
# SSM Run Command Comment prefix to map invocations to recipe steps (≤100 chars).
AMI_BUILD_COMMENT_PREFIX = "desk-ami-build:"
# Single tar object per copy step under files/copy/<i>/ (async AMI staging).
AMI_COPY_BUNDLE_NAME = "bundle.tar"
