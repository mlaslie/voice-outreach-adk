# Common tasks. ENV selects infra/envs/<ENV> and config/environments/<ENV>.yaml.
ENV ?= demo
TF  := terraform -chdir=infra/envs/$(ENV)

.PHONY: setup test lint pii tf-bootstrap tf-init tf-plan tf-apply spike-harness spike-serve deploy smoke docs-config bootstrap

setup:            ## install deps and git hooks
	uv sync --all-packages
	uv run pre-commit install --hook-type pre-commit --hook-type pre-push

test:             ## unit tests (no cloud calls)
	uv run pytest

lint:
	uv run ruff check .
	terraform fmt -check -recursive infra

pii:              ## scan every tracked + untracked-not-ignored file
	uv run python scripts/pii_scan.py

tf-bootstrap:     ## one-time: state bucket + backend.hcl
	scripts/bootstrap/00-state-bucket.sh $(ENV)

tf-init:
	$(TF) init -backend-config=backend.hcl

tf-plan:
	$(TF) plan -out=tfplan

tf-apply:
	$(TF) apply tfplan

spike-harness:    ## phase-0 voice spike: simulated patient vs Gemini Live (costs model time)
	cd spikes/voice && uv run python -m voice_spike.harness

spike-serve:      ## run the spike server locally (softphone at http://localhost:8080/softphone)
	cd spikes/voice && uv run uvicorn voice_spike.server:app --port 8080

deploy:           ## build at HEAD and roll out: make deploy SERVICES="gateway api ui"
	scripts/deploy.sh $(ENV) $(SERVICES)

smoke:            ## deployed end-to-end checks (API as the dashboard + spoken softphone call)
	scripts/e2e/api_smoke.sh $(ENV)

docs-config:      ## regenerate docs/CONFIG.md from the config models
	uv run python scripts/gen_config_doc.py

bootstrap: tf-bootstrap tf-init tf-plan tf-apply  ## base infrastructure for a new environment (see docs/DEPLOY.md)

eval-voice:       ## persona eval with pass rates (real Gemini Live; ~10 min): make eval-voice RUNS=3
	uv run python evals/voice_personas.py --runs $(or $(RUNS),3) --concurrency 3
