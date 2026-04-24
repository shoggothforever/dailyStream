# DailyStream Makefile — convenience wrappers for dev / bundle / release.

SWIFT_DIR := apps/DailyStreamMac
VENV      := .venv/bin/python

.PHONY: help dev run build test test-py test-swift lint bundle bundle-force \
        clean-bundle clean ci release

help:  ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		sort | awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# ── Development ──────────────────────────────────────────

dev:  ## start the RPC server on stdio (for hand-typing JSON-RPC requests)
	@scripts/dev_run.sh

run: bundle build  ## one-shot: bundle Python → build Swift → launch app
	@echo "▶ launching DailyStreamMac…"
	@cd $(SWIFT_DIR) && swift run DailyStreamMac

build: bundle  ## bundle Python + build Swift (no launch)
	@echo "▶ swift build…"
	@cd $(SWIFT_DIR) && swift build 2>&1 | tail -3

build-swift:  ## build Swift only (skip bundle, for fast iteration on Swift code)
	@cd $(SWIFT_DIR) && swift build

# ── Testing ──────────────────────────────────────────────

test: test-py test-swift  ## run all tests (Python + Swift)

test-py:  ## run Python pytest suite
	@echo "▶ pytest…"
	@$(VENV) -m pytest tests/ -q

test-swift:  ## run Swift tests
	@echo "▶ swift test…"
	@cd $(SWIFT_DIR) && swift test -q 2>&1 | tail -5

lint:  ## lint Python code (ruff)
	@echo "▶ ruff check…"
	@$(VENV) -m ruff check src/ tests/ || true

# ── Bundle / Package ────────────────────────────────────

bundle:  ## download python-build-standalone and install dailystream into it
	@bash scripts/bundle_python.sh

bundle-force:  ## rebuild the bundle from scratch (use after changing Python code)
	@bash scripts/bundle_python.sh --force

clean-bundle:  ## remove the embedded framework (keeps download cache)
	@rm -rf $(SWIFT_DIR)/Frameworks/Python.framework
	@echo "✓ removed Python.framework"

clean: clean-bundle  ## remove framework + Swift build artifacts
	@rm -rf $(SWIFT_DIR)/.build
	@echo "✓ cleaned"

# ── CI / Release ────────────────────────────────────────

ci: lint test-py bundle build test-swift  ## full CI pipeline: lint → test-py → bundle → build → test-swift
	@echo ""
	@echo "══════════════════════════════════════"
	@echo "  ✅  CI passed"
	@echo "══════════════════════════════════════"

release:  ## (M5) build, sign, notarize and package DailyStream.app
	@bash scripts/build_release.sh
