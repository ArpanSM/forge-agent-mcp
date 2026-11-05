PYTHON ?= python3
PIP_FLAGS ?=
PYTHONPATH ?= src
CASE ?=
OFFLINE ?= 0
LIVE_PROVIDER ?= orcarouter
LIVE_INPUT_PRICE_PER_1K ?=
LIVE_OUTPUT_PRICE_PER_1K ?=

export PYTHONPATH

.PHONY: install corpus test test-live lint extract review gateway gateway-client gateway-demo write-demo console demo demo-auto eval eval-live

install:
	$(PYTHON) -m pip install $(PIP_FLAGS) -e ".[dev]"

corpus:
	$(PYTHON) -m forge_agent_mcp.corpus generate --out data/corpus

test:
	$(PYTHON) -m pytest

test-live:
	FORGE_LIVE_MODEL=1 $(PYTHON) -m pytest -m live

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

extract:
	@test -n "$(CASE)" || (echo "usage: make extract CASE=<case_id>" >&2; exit 2)
	@test -f data/corpus/corpus.json || $(MAKE) corpus
	$(PYTHON) -m forge_agent_mcp.extraction --case "$(CASE)" $(if $(filter 1,$(OFFLINE)),--offline,)

review:
	@test -n "$(CASE)" || (echo "usage: make review CASE=<case_id>" >&2; exit 2)
	@test -f data/corpus/corpus.json || $(MAKE) corpus
	$(PYTHON) -m forge_agent_mcp.rules --case "$(CASE)"

gateway:
	$(PYTHON) -m forge_agent_mcp.gateway serve

gateway-client:
	$(PYTHON) -m forge_agent_mcp.gateway client

gateway-demo:
	$(PYTHON) -m forge_agent_mcp.gateway demo

write-demo:
	$(PYTHON) -m forge_agent_mcp.gateway write-demo

console:
	@test -f data/corpus/corpus.json || $(MAKE) corpus
	$(PYTHON) -m forge_agent_mcp.console demo --serve

demo:
	@test -f data/corpus/corpus.json || $(MAKE) corpus
	$(PYTHON) -m forge_agent_mcp.console demo --auto --serve

demo-auto:
	@test -f data/corpus/corpus.json || $(MAKE) corpus
	$(PYTHON) -m forge_agent_mcp.console demo --auto

eval:
	@test -f data/corpus/corpus.json || $(MAKE) corpus
	$(PYTHON) -m forge_agent_mcp.evals --out reports

eval-live:
	@test -f data/corpus/corpus.json || $(MAKE) corpus
	$(PYTHON) -m forge_agent_mcp.evals --out reports/live --provider "$(LIVE_PROVIDER)" $(if $(LIVE_INPUT_PRICE_PER_1K),--input-price-per-1k $(LIVE_INPUT_PRICE_PER_1K)) $(if $(LIVE_OUTPUT_PRICE_PER_1K),--output-price-per-1k $(LIVE_OUTPUT_PRICE_PER_1K))
