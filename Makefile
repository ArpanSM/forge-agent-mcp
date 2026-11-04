PYTHON ?= python3
PIP_FLAGS ?=
PYTHONPATH ?= src
CASE ?=
OFFLINE ?= 0

export PYTHONPATH

.PHONY: install corpus test test-live lint extract gateway gateway-client gateway-demo

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

gateway:
	$(PYTHON) -m forge_agent_mcp.gateway serve

gateway-client:
	$(PYTHON) -m forge_agent_mcp.gateway client

gateway-demo:
	$(PYTHON) -m forge_agent_mcp.gateway demo
