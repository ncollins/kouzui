.PHONY: typecheck lint integration-test unit-test check

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

typecheck:
	uv run mypy .
	uv run ty check .

lint:
	uv run ruff check .

check: format-check lint typecheck unit-test

TEST_DATA_ABS    := $(abspath integration_tests/test_data)
POD_MANIFEST_TMP := /tmp/kouzui-pod.yml

integration-test:
	uv run integration_tests/test_data.py create-incomplete-files integration_tests/test_data test_file.bin http://localhost:8000/announce
	podman build -t kouzui-tracker -f integration_tests/Dockerfile.tracker integration_tests/
	podman build -t kouzui-clients -f integration_tests/Dockerfile.clients .
	podman build -t single-client -f integration_tests/Dockerfile.clients .
	sed 's|__TEST_DATA_DIR__|$(TEST_DATA_ABS)|' integration_tests/pod.yml > $(POD_MANIFEST_TMP)
	podman kube play --replace $(POD_MANIFEST_TMP)
	podman wait kouzui-integration-test-single-client; \
	podman wait kouzui-integration-test-clients; \
	result=$$?; \
	podman kube down $(POD_MANIFEST_TMP); \
	exit $$result
	uv run integration_tests/test_data.py verify-complete-files integration_tests/test_data test_file.bin

unit-test:
	uv run pytest .

install:
	uv tool install --python python3.11 .