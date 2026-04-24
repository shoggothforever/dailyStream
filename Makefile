# DailyStream Makefile — convenience wrappers for dev / bundle / release.

.PHONY: help dev test lint bundle clean-bundle release

help:  ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		sort | awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

dev:  ## start the RPC server on stdio (for hand-typing JSON-RPC requests)
	@scripts/dev_run.sh

test:  ## run the full pytest suite
	@.venv/bin/python -m pytest tests/ -q

bundle:  ## download python-build-standalone and install dailystream into it
	@bash scripts/bundle_python.sh

bundle-force:  ## rebuild the bundle from scratch
	@bash scripts/bundle_python.sh --force

clean-bundle:  ## remove the embedded framework (keeps download cache)
	@rm -rf apps/DailyStreamMac/Frameworks/Python.framework
	@echo "✓ removed Python.framework"

release:  ## (M5) build, sign, notarize and package DailyStream.app
	@bash scripts/build_release.sh
